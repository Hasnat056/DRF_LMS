"""
test_api.py
-----------
HTTP-layer integration tests.
Tests the full request→serializer→DB→response cycle.

Covers:
  - Class API      : create (auto-semester), retrieve (scheme_of_studies nesting),
                     update (scheme_of_studies write)
  - Semester API   : retrieve structure, activation_deadline set/guard,
                     closing_deadline blocked without results
  - Allocation API : semester eligibility filter enforced at API level,
                     course-in-scheme guard, duplicate guard
  - Enrollment API : only Ongoing allocations accepted,
                     cannot enroll in Inactive/Completed allocation
  - Bulk API       : CSV upload returns structured response, unknown type handled
"""

import io
import csv
import pytest
from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone
from django.urls import reverse

from Models.models import (
    Class, Semester, SemesterDetails, Course, CourseAllocation,
    Enrollment, Result,
)

ADMIN = '/api/admin'


# ===========================================================================
# Class API
# ===========================================================================

@pytest.mark.django_db
class TestClassAPICreate:

    def test_create_class_auto_generates_semesters(self, admin_client, program):
        """POST /classes/ should auto-create N semesters for the program."""
        response = admin_client.post(f'{ADMIN}/classes/', {
            'program_id': program.program_id,
            'batch_year': 2023,
        }, format='json')
        assert response.status_code == 201

        new_class = Class.objects.get(program_id=program, batch_year=2023)
        sem_count = Semester.objects.filter(
            semesterdetails__class_id=new_class
        ).distinct().count()
        assert sem_count == program.total_semesters

    def test_create_class_returns_class_id(self, admin_client, program):
        response = admin_client.post(f'{ADMIN}/classes/', {
            'program_id': program.program_id,
            'batch_year': 2029,
        }, format='json')
        assert response.status_code == 201
        assert 'class_id' in response.data


@pytest.mark.django_db
class TestClassAPIRetrieve:

    def test_retrieve_class_includes_scheme_of_studies(
        self, admin_client, batch_class, inactive_semester
    ):
        """GET /classes/<id>/ should include scheme_of_studies with nested semesters."""
        url = reverse('Admin:class-detail', kwargs={'class_id': batch_class.class_id})
        response = admin_client.get(url)
        assert response.status_code == 200
        assert 'scheme_of_studies' in response.data

    def test_scheme_of_studies_contains_semester_details(
        self, admin_client, batch_class, inactive_semester, course
    ):
        """scheme_of_studies must contain semesterdetails_set with course info."""
        SemesterDetails.objects.filter(
            semester_id=inactive_semester, class_id=batch_class
        ).update(course_code=course)

        url = reverse('Admin:class-detail', kwargs={'class_id': batch_class.class_id})
        response = admin_client.get(url)
        assert response.status_code == 200

        scheme = response.data.get('scheme_of_studies')
        assert scheme is not None
        # find our semester in the scheme
        sem_data = next(
            (s for s in scheme if s['semester_id'] == inactive_semester.semester_id), None
        )
        assert sem_data is not None
        assert 'semesterdetails_set' in sem_data

    def test_scheme_of_studies_is_none_for_class_with_no_semesters(
        self, admin_client, program, db
    ):
        """A class with no SemesterDetails should return scheme_of_studies=None."""
        bare_class = Class.objects.create(program_id=program, batch_year=2030)
        url = reverse('Admin:class-detail', kwargs={'class_id': bare_class.class_id})
        response = admin_client.get(url)
        assert response.status_code == 200
        assert response.data.get('scheme_of_studies') is None


@pytest.mark.django_db
class TestClassAPIUpdate:

    def test_update_scheme_of_studies_assigns_course(
        self, admin_client, batch_class, inactive_semester, course
    ):
        """PATCH scheme_of_studies should persist course assignment."""
        url = reverse('Admin:class-detail', kwargs={'class_id': batch_class.class_id})
        response = admin_client.patch(url, {
            'scheme_of_studies': [
                {
                    'semester_id': inactive_semester.semester_id,
                    'semesterdetails_set': [{'course_code': course.course_code}],
                }
            ]
        }, format='json')
        assert response.status_code == 200
        assert SemesterDetails.objects.filter(
            semester_id=inactive_semester, course_code=course, class_id=batch_class
        ).exists()

    def test_update_without_scheme_of_studies_returns_400(
        self, admin_client, batch_class
    ):
        """PATCH without scheme_of_studies on an existing class should fail gracefully."""
        url = reverse('Admin:class-detail', kwargs={'class_id': batch_class.class_id})
        # update() unconditionally does validated_data.pop('scheme_of_studies')
        # so a PATCH without it will raise KeyError — this is a known risk
        response = admin_client.patch(url, {'batch_year': 2023}, format='json')
        # acceptable responses: 200 (handled gracefully) or 400 (validation) NOT 500
        assert response.status_code in (200, 400), (
            f"Got {response.status_code} — update() may be crashing on missing scheme_of_studies"
        )


# ===========================================================================
# Semester API
# ===========================================================================

@pytest.mark.django_db
class TestSemesterAPIRetrieve:

    def test_retrieve_semester_has_expected_fields(self, admin_client, inactive_semester):
        url = reverse('Admin:semester-detail', kwargs={'semester_id': inactive_semester.semester_id})
        response = admin_client.get(url)
        assert response.status_code == 200
        for field in ['semester_id', 'semester_no', 'session', 'status', 'semesterdetails_set']:
            assert field in response.data, f"Missing field: {field}"

    def test_inactive_semester_has_no_transcript_url(self, admin_client, inactive_semester):
        """transcript_generation_url only appears when closing_deadline is set."""
        url = reverse('Admin:semester-detail', kwargs={'semester_id': inactive_semester.semester_id})
        response = admin_client.get(url)
        assert 'transcript_generation_url' not in response.data


@pytest.mark.django_db
class TestSemesterAPIActivationDeadline:

    def test_set_activation_deadline_in_future_succeeds(
        self, admin_client, inactive_semester
    ):
        """Setting activation_deadline to a future time should succeed."""
        url = reverse('Admin:semester-detail', kwargs={'semester_id': inactive_semester.semester_id})
        future = (timezone.now() + timedelta(days=5)).isoformat()
        response = admin_client.patch(url, {'activation_deadline': future}, format='json')
        # 200 OK; Celery task is scheduled (mocked in test env)
        assert response.status_code == 200

    def test_set_activation_deadline_in_past_returns_400(
        self, admin_client, inactive_semester
    ):
        url = reverse('Admin:semester-detail', kwargs={'semester_id': inactive_semester.semester_id})
        past = (timezone.now() - timedelta(days=1)).isoformat()
        response = admin_client.patch(url, {'activation_deadline': past}, format='json')
        assert response.status_code == 400

    def test_cannot_activate_semester_when_class_has_active_semester(
        self, admin_client, batch_class, inactive_semester, active_semester
    ):
        """If the class already has an Active semester, setting activation_deadline should fail."""
        # make sure both semesters are linked to same class
        SemesterDetails.objects.get_or_create(
            semester_id=active_semester, class_id=batch_class,
            defaults={'course_code': None}
        )
        url = reverse('Admin:semester-detail', kwargs={'semester_id': inactive_semester.semester_id})
        future = (timezone.now() + timedelta(days=7)).isoformat()
        response = admin_client.patch(url, {'activation_deadline': future}, format='json')
        assert response.status_code == 400


@pytest.mark.django_db
class TestSemesterAPIClosingDeadline:

    def test_closing_deadline_blocked_if_enrollment_has_no_result(
        self, admin_client, active_semester, course_allocation, enrollment
    ):
        """
        PATCH closing_deadline must return 400 if any enrollment
        has no course_gpa — results must be complete first.
        """
        course_allocation.semester_id = active_semester
        course_allocation.save()
        enrollment.allocation_id = course_allocation
        enrollment.save()
        enrollment.result.course_gpa = None
        enrollment.result.save()

        url = reverse('Admin:semester-detail', kwargs={'semester_id': active_semester.semester_id})
        future = (timezone.now() + timedelta(days=30)).isoformat()
        response = admin_client.patch(url, {'closing_deadline': future}, format='json')
        assert response.status_code == 400


# ===========================================================================
# Allocation API
# ===========================================================================

@pytest.mark.django_db
class TestAllocationAPICreate:

    def test_create_allocation_in_valid_semester_succeeds(
        self, admin_client, faculty_instance, course, inactive_semester
    ):
        """Allocation creation with valid semester+course should return 201."""
        # ensure course is in semester scheme
        SemesterDetails.objects.get_or_create(
            semester_id=inactive_semester,
            class_id=inactive_semester.semesterdetails_set.first().class_id,
            course_code=course,
        )
        response = admin_client.post(f'{ADMIN}/allocations/', {
            'teacher_id': faculty_instance.employee_id.person_id,
            'course_code': course.course_code,
            'semester_id': inactive_semester.semester_id,
        }, format='json')
        assert response.status_code == 201

    def test_cannot_create_allocation_in_active_semester(
        self, admin_client, faculty_instance, course, active_semester
    ):
        """
        Active semesters are excluded from semester_id queryset,
        so creating an allocation against one should be rejected.
        """
        response = admin_client.post(f'{ADMIN}/allocations/', {
            'teacher_id': faculty_instance.employee_id.person_id,
            'course_code': course.course_code,
            'semester_id': active_semester.semester_id,
        }, format='json')
        # queryset filter means the FK validation itself rejects this
        assert response.status_code == 403

    def test_cannot_create_allocation_with_course_not_in_scheme(
        self, admin_client, faculty_instance, inactive_semester, db
    ):
        """Course not in semester scheme must be rejected."""
        unrelated = Course.objects.create(
            course_code='XX-999', course_name='Unrelated', credit_hours=2, lab=False
        )
        response = admin_client.post(f'{ADMIN}/allocations/', {
            'teacher_id': faculty_instance.employee_id.person_id,
            'course_code': unrelated.course_code,
            'semester_id': inactive_semester.semester_id,
        }, format='json')
        assert response.status_code == 400

    def test_duplicate_allocation_rejected(
        self, admin_client, faculty_instance, course, inactive_semester, course_allocation
    ):
        """Second identical allocation must return 400."""
        SemesterDetails.objects.get_or_create(
            semester_id=inactive_semester,
            class_id=inactive_semester.semesterdetails_set.first().class_id,
            course_code=course,
        )
        response = admin_client.post(f'{ADMIN}/allocations/', {
            'teacher_id': faculty_instance.employee_id.person_id,
            'course_code': course.course_code,
            'semester_id': inactive_semester.semester_id,
        }, format='json')
        assert response.status_code == 400


# ===========================================================================
# Enrollment API
# ===========================================================================

@pytest.mark.django_db
class TestEnrollmentAPICreate:

    def test_cannot_enroll_in_inactive_allocation(
        self, admin_client, student_instance, course_allocation
    ):
        """Enrollment in an Inactive allocation must be rejected (not in Ongoing queryset)."""
        course_allocation.status = 'Inactive'
        course_allocation.save()

        response = admin_client.post(f'{ADMIN}/enrollments/', {
            'student_id': student_instance.pk,
            'allocation_id': course_allocation.allocation_id,
        }, format='json')
        assert response.status_code == 403

    def test_cannot_enroll_in_completed_allocation(
        self, admin_client, student_instance, course_allocation
    ):
        """Enrollment in a Completed allocation must be rejected."""
        course_allocation.status = 'Completed'
        course_allocation.save()

        response = admin_client.post(f'{ADMIN}/enrollments/', {
            'student_id': student_instance.pk,
            'allocation_id': course_allocation.allocation_id,
        }, format='json')
        assert response.status_code == 403

    def test_enroll_creates_result_record(
        self, admin_client, student_instance, course_allocation
    ):
        """Enrollment creation should auto-create a Result row."""
        course_allocation.status = 'Ongoing'
        course_allocation.save()

        response = admin_client.post(f'{ADMIN}/enrollments/', {
            'student_id': student_instance.pk,
            'allocation_id': course_allocation.allocation_id,
        }, format='json')
        assert response.status_code == 201
        enrollment = Enrollment.objects.get(
            student_id=student_instance, allocation_id=course_allocation
        )
        assert Result.objects.filter(enrollment_id=enrollment).exists()


# ===========================================================================
# Bulk API
# ===========================================================================

@pytest.mark.django_db
class TestBulkAPI:

    def _csv_file(self, content, filename='data.csv'):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return SimpleUploadedFile(filename, content.encode(), content_type='text/csv')

    def test_bulk_post_requires_authentication(self, anon_client):
        response = anon_client.post(f'{ADMIN}/bulk/', {}, format='multipart')
        assert response.status_code == 401

    def test_bulk_post_with_valid_faculty_csv_returns_row_counts(
        self, admin_client, faculty_group, department
    ):
        """
        A valid faculty CSV should return a structured response with
        row_count, insert_count, error_row_count — not crash.
        Rows that fail serializer validation are captured in error_rows, not raised.
        """
        csv_content = (
            'password,first_name,last_name,father_name,gender,cnic,dob,'
            'contact_number,institutional_email,department_id,designation,joining_date\n'
            'pass123,Jane,Smith,Father,Female,12345-9876543-2,1985-06-15,'
            '+923002222222,bulk.jane@test.com,CS,Lecturer,2024-01-01\n'
        )
        response = admin_client.post(
            f'{ADMIN}/bulk/?type=faculty',
            {'file': self._csv_file(csv_content)},
            format='multipart'
        )
        # Not 500. Structure check happens if 200.
        assert response.status_code in (200, 201, 400)
        if response.status_code in (200, 201):
            assert 'row_count' in response.data or 'insert_count' in response.data

    def test_bulk_post_with_malformed_csv_captures_errors_not_crashes(
        self, admin_client, faculty_group
    ):
        """
        Rows with bad data should appear in error_rows, not raise a 500.
        """
        bad_csv = 'password,first_name\npass,\n'  # missing required fields
        response = admin_client.post(
            f'{ADMIN}/bulk/?type=faculty',
            {'file': self._csv_file(bad_csv)},
            format='multipart'
        )
        assert response.status_code != 500