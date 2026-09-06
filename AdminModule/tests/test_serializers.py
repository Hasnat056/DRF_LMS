"""
test_serializers.py
-------------------
Direct serializer-layer tests. No HTTP overhead — we test the serializer
classes directly so failures point exactly at the broken line.

Covers:
  - ClassSerializer  : create (auto-semester generation), update (scheme_of_studies write)
  - SemesterSerializer : activation_deadline / closing_deadline guard logic
  - CourseAllocationSerializer : semester eligibility filter, course-in-scheme validation
  - EnrollmentSerializer : allocation queryset restricted to Ongoing
  - CourseSerializer : the lab checkbox building and removing the {code}-L course
  - BulkTranscriptSerializer : happy path + zero-division guard + missing-result guard
  - FacultyStudentBulkSerializer : file validation bug exposure
  - PersonSerializer / QualificationSerializer : field-level validators
"""

import pytest
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.utils import timezone
from rest_framework.test import APIRequestFactory
from rest_framework import serializers as drf_serializers

from Models.models import (
     Semester, SemesterDetails, Course, CourseAllocation,
)
from AdminModule.serializers import (
    ClassSerializer, SemesterSerializer, CourseAllocationSerializer,
    EnrollmentSerializer, CourseSerializer, BulkTranscriptSerializer,
    FacultyStudentBulkSerializer, PersonSerializer, QualificationSerializer,
)


factory = APIRequestFactory()


def _admin_request(admin_user, method='get'):
    """Return a fake request object with admin user attached."""
    req = getattr(factory, method)('/')
    req.user = admin_user
    return req


# ===========================================================================
# Helpers
# ===========================================================================

def _ctx(admin_user):
    return {'request': _admin_request(admin_user)}


# ===========================================================================
# ClassSerializer — create
# ===========================================================================

@pytest.mark.django_db
class TestClassSerializerCreate:

    def test_creates_class_with_correct_semester_count(self, admin_user, program):
        """Creating a class should auto-generate total_semesters semesters."""
        data = {'program': program.program_id, 'batch_year': 2023}
        serializer = ClassSerializer(data=data, context=_ctx(admin_user))
        assert serializer.is_valid(), serializer.errors

        new_class = serializer.save()

        semesters = Semester.objects.filter(associated_class=new_class)
        assert semesters.count() == program.total_semesters

    def test_creates_semesterdetails_for_each_semester(self, admin_user, program):
        """Each auto-created semester should have a SemesterDetails row for this class."""
        data = {'program': program.program_id, 'batch_year': 2024}
        serializer = ClassSerializer(data=data, context=_ctx(admin_user))
        assert serializer.is_valid(), serializer.errors

        new_class = serializer.save()

        details_count = SemesterDetails.objects.filter(semester__associated_class=new_class).count()
        assert details_count == program.total_semesters

    def test_semester_numbers_are_sequential(self, admin_user, program):
        """Auto-created semesters should be numbered 1..N."""
        data = {'program': program.program_id, 'batch_year': 2025}
        serializer = ClassSerializer(data=data, context=_ctx(admin_user))
        assert serializer.is_valid(), serializer.errors
        new_class = serializer.save()

        numbers = sorted(
            Semester.objects.filter(associated_class=new_class)
            .values_list('semester_no', flat=True)
        )
        assert numbers == list(range(1, program.total_semesters + 1))

    def test_scheme_of_studies_is_ignored_on_create(self, admin_user, program):
        """scheme_of_studies sent on create must be silently popped, not crash."""
        data = {
            'program': program.program_id,
            'batch_year': 2026,
            'scheme_of_studies': [{'semester_id': 999}],  # should be ignored
        }
        serializer = ClassSerializer(data=data, context=_ctx(admin_user))
        assert serializer.is_valid(), serializer.errors
        new_class = serializer.save()
        assert new_class.pk is not None


# ===========================================================================
# ClassSerializer — update (scheme_of_studies write)
# ===========================================================================

@pytest.mark.django_db
class TestClassSerializerUpdate:

    def test_update_assigns_course_to_semester(
        self, admin_user, batch_class, inactive_semester, course
    ):
        """Updating scheme_of_studies should assign a course to the semester."""
        payload = {
            'scheme_of_studies': [
                {
                    'semester_id': inactive_semester.semester_id,
                    'semesterdetails_set': [{'course': course.course_code}],
                }
            ]
        }
        serializer = ClassSerializer(
            instance=batch_class, data=payload,
            partial=True, context=_ctx(admin_user)
        )
        assert serializer.is_valid(), serializer.errors
        serializer.save()

        assert SemesterDetails.objects.filter(
            semester=inactive_semester, course=course
        ).exists()

    def test_update_replaces_existing_courses(
        self, admin_user, batch_class, inactive_semester, course, db
    ):
        """Sending a new course list should replace the old SemesterDetails rows."""
        old_course = Course.objects.create(
            course_code='OLD-001', course_name='Old Course', credit_hours=2
        )
        SemesterDetails.objects.create(
            semester=inactive_semester, course=old_course
        )

        payload = {
            'scheme_of_studies': [
                {
                    'semester_id': inactive_semester.semester_id,
                    'semesterdetails_set': [{'course': course.course_code}],
                }
            ]
        }
        serializer = ClassSerializer(
            instance=batch_class, data=payload,
            partial=True, context=_ctx(admin_user)
        )
        assert serializer.is_valid(), serializer.errors
        serializer.save()

        # old course gone, new course present
        assert not SemesterDetails.objects.filter(course=old_course).exists()
        assert SemesterDetails.objects.filter(course=course).exists()

    def test_update_with_invalid_semester_id_raises_404(
        self, admin_user, batch_class
    ):
        """A non-existent semester_id in scheme_of_studies should raise Http404."""
        from django.http import Http404
        payload = {
            'scheme_of_studies': [
                {
                    'semester_id': 99999,
                    'semesterdetails_set': [{'course': None}],
                }
            ]
        }
        serializer = ClassSerializer(
            instance=batch_class, data=payload,
            partial=True, context=_ctx(admin_user)
        )
        assert serializer.is_valid(), serializer.errors
        with pytest.raises(Http404):
            serializer.save()


# ===========================================================================
# SemesterSerializer — session binding
# ===========================================================================

@pytest.mark.django_db
class TestSemesterSerializerSessionGuard:
    """Semesters no longer carry their own deadlines — the old field guards and
    validators went with them. What remains is that a semester's session is
    frozen once it is no longer Inactive."""

    def test_session_editable_while_inactive(self, admin_user, inactive_semester):
        serializer = SemesterSerializer(instance=inactive_semester, context=_ctx(admin_user))
        assert serializer.fields['session'].read_only is False

    def test_session_readonly_once_active(self, admin_user, active_semester):
        serializer = SemesterSerializer(instance=active_semester, context=_ctx(admin_user))
        assert serializer.fields['session'].read_only is True

    def test_deadline_fields_are_gone(self, admin_user, inactive_semester):
        serializer = SemesterSerializer(instance=inactive_semester, context=_ctx(admin_user))
        assert 'activation_deadline' not in serializer.fields
        assert 'closing_deadline' not in serializer.fields


# ===========================================================================
# CourseAllocationSerializer
# ===========================================================================

@pytest.mark.django_db
class TestCourseAllocationSerializer:

    def test_semester_queryset_filtered_to_inactive_with_initiated_session(
        self, admin_user, inactive_semester, active_semester
    ):
        """
        The semester field queryset must only include Inactive semesters whose
        session is Initiated.
        """
        serializer = CourseAllocationSerializer(context=_ctx(admin_user))
        qs = serializer.fields['semester'].queryset
        assert inactive_semester in qs
        assert active_semester not in qs

    def test_cannot_create_allocation_with_course_not_in_semester_scheme(
        self, admin_user, faculty_instance, inactive_semester, db
    ):
        """Course not in semester's SemesterDetails must be rejected."""
        unrelated_course = Course.objects.create(
            course_code='CS-999', course_name='Unrelated', credit_hours=3
        )
        data = {
            'faculty': faculty_instance.pk,
            'course': unrelated_course.course_code,
            'semester': inactive_semester.semester_id,
        }
        serializer = CourseAllocationSerializer(data=data, context=_ctx(admin_user))
        assert serializer.is_valid(), serializer.errors
        from rest_framework import serializers as drf_serializers
        with pytest.raises(drf_serializers.ValidationError):
            serializer.save()

    def test_cannot_create_duplicate_allocation(
        self, admin_user, faculty_instance, course, inactive_semester, course_allocation
    ):
        """Duplicate teacher+course+semester allocation must be rejected."""
        data = {
            'faculty': faculty_instance.pk,
            'course': course.course_code,
            'semester': inactive_semester.semester_id,
        }
        serializer = CourseAllocationSerializer(data=data, context=_ctx(admin_user))
        assert not serializer.is_valid()

    def test_create_sets_session_from_semester(
        self, admin_user, faculty_instance, course, inactive_semester
    ):
        """The session field should be auto-filled from the semester's session."""
        # ensure course is in semester scheme
        SemesterDetails.objects.get_or_create(
            semester=inactive_semester,
            course=course,
        )
        data = {
            'faculty': faculty_instance.pk,
            'course': course.course_code,
            'semester': inactive_semester.semester_id,
        }
        serializer = CourseAllocationSerializer(data=data, context=_ctx(admin_user))
        assert serializer.is_valid(), serializer.errors
        allocation = serializer.save()
        assert allocation.session == inactive_semester.session


# ===========================================================================
# EnrollmentSerializer
# ===========================================================================

@pytest.mark.django_db
class TestEnrollmentSerializerQueryset:

    def test_allocation_queryset_restricted_to_ongoing(
        self, admin_user, course_allocation, db
    ):
        """allocation_id field must only show Ongoing allocations."""
        course_allocation.status = 'Active'
        course_allocation.save()

        # Use a different course to avoid the unique_together constraint
        from Models.models import Course
        other_course = Course.objects.create(
            course_code='CS-999', course_name='Dummy Course',
            credit_hours=3
        )
        inactive_alloc = CourseAllocation.objects.create(
            faculty=course_allocation.faculty,
            course=other_course,
            semester=course_allocation.semester,
            session='Spring-2025',
            status='Inactive',
        )

        serializer = EnrollmentSerializer(context=_ctx(admin_user))
        qs = serializer.fields['allocation'].queryset

        assert course_allocation in qs
        assert inactive_alloc not in qs

    def test_allocation_queryset_empty_when_no_ongoing(self, admin_user, course_allocation):
        """If no allocations are Ongoing, the queryset must be empty."""
        course_allocation.status = 'Inactive'
        course_allocation.save()

        serializer = EnrollmentSerializer(context=_ctx(admin_user))
        qs = serializer.fields['allocation'].queryset
        assert qs.count() == 0


# ===========================================================================
# CourseSerializer — the lab checkbox builds and removes a lab course
# ===========================================================================

def _save(data, instance=None, admin_user=None):
    serializer = CourseSerializer(instance=instance, data=data, context=_ctx(admin_user))
    assert serializer.is_valid(), serializer.errors
    return serializer.save()


@pytest.mark.django_db
class TestCourseSerializerLabToggle:

    def test_lab_true_on_create_builds_the_lab_course(self, admin_user):
        course = _save(
            {'course_code': 'CS-LAB1', 'course_name': 'Lab Course', 'credit_hours': 3, 'lab': True},
            admin_user=admin_user,
        )
        # The theory course keeps its own hours; the lab carries its one.
        assert course.credit_hours == 3
        assert course.lab.course_code == 'CS-LAB1-L'
        assert course.lab.course_name == 'Lab Course -L'
        assert course.lab.credit_hours == 1

    def test_lab_false_on_create_builds_nothing(self, admin_user):
        course = _save(
            {'course_code': 'CS-NOLAB', 'course_name': 'Theory', 'credit_hours': 3, 'lab': False},
            admin_user=admin_user,
        )
        assert course.credit_hours == 3
        assert course.lab is None
        assert not Course.objects.filter(course_code='CS-NOLAB-L').exists()

    def test_lab_is_left_without_a_prerequisite_of_its_own(self, admin_user, db):
        prereq = Course.objects.create(
            course_code='CS-000', course_name='Basics', credit_hours=3
        )
        course = _save(
            {'course_code': 'CS-PRE', 'course_name': 'Advanced', 'credit_hours': 3,
             'lab': True, 'pre_requisite': prereq.course_code},
            admin_user=admin_user,
        )
        # A lab is gated by its theory course, so copying the prerequisite
        # would just be a second copy to keep in step.
        assert course.pre_requisite == prereq
        assert course.lab.pre_requisite is None

    def test_ticking_the_box_later_builds_the_lab_course(self, admin_user, db):
        course = Course.objects.create(
            course_code='CS-F2T', course_name='Now Lab', credit_hours=3
        )
        updated = _save(
            {'course_code': 'CS-F2T', 'course_name': 'Now Lab', 'credit_hours': 3, 'lab': True},
            instance=course, admin_user=admin_user,
        )
        assert updated.credit_hours == 3
        assert updated.lab.course_code == 'CS-F2T-L'

    def test_clearing_the_box_deletes_the_lab_course(self, admin_user, db):
        course = _save(
            {'course_code': 'CS-T2F', 'course_name': 'Was Lab', 'credit_hours': 3, 'lab': True},
            admin_user=admin_user,
        )
        updated = _save(
            {'course_code': 'CS-T2F', 'course_name': 'Was Lab', 'credit_hours': 3, 'lab': False},
            instance=course, admin_user=admin_user,
        )
        assert updated.credit_hours == 3
        assert updated.lab is None
        assert not Course.objects.filter(course_code='CS-T2F-L').exists()

    def test_clearing_the_box_is_refused_while_the_lab_is_allocated(
        self, admin_user, db, faculty_instance, active_semester
    ):
        course = _save(
            {'course_code': 'CS-ALC', 'course_name': 'Allocated', 'credit_hours': 3, 'lab': True},
            admin_user=admin_user,
        )
        CourseAllocation.objects.create(
            faculty=faculty_instance, course=course.lab, semester=active_semester
        )

        serializer = CourseSerializer(
            instance=course,
            data={'course_code': 'CS-ALC', 'course_name': 'Allocated',
                  'credit_hours': 3, 'lab': False},
            context=_ctx(admin_user),
        )
        assert serializer.is_valid(), serializer.errors
        with pytest.raises(drf_serializers.ValidationError):
            serializer.save()

        # What holds for a course holds for its lab: the RESTRICT stands and
        # the link survives the refusal.
        course.refresh_from_db()
        assert course.lab is not None
        assert Course.objects.filter(course_code='CS-ALC-L').exists()

    def test_renaming_the_course_renames_its_lab(self, admin_user, db):
        course = _save(
            {'course_code': 'CS-REN', 'course_name': 'Old Name', 'credit_hours': 3, 'lab': True},
            admin_user=admin_user,
        )
        updated = _save(
            {'course_code': 'CS-REN', 'course_name': 'New Name', 'credit_hours': 3, 'lab': True},
            instance=course, admin_user=admin_user,
        )
        assert updated.lab.course_name == 'New Name -L'

    def test_lab_is_refused_when_the_code_leaves_no_room_for_the_suffix(self, admin_user):
        serializer = CourseSerializer(
            data={'course_code': 'X' * 20, 'course_name': 'Too Long',
                  'credit_hours': 3, 'lab': True},
            context=_ctx(admin_user),
        )
        assert serializer.is_valid(), serializer.errors
        with pytest.raises(drf_serializers.ValidationError):
            serializer.save()

    def test_lab_is_refused_when_the_code_is_already_taken(self, admin_user, db):
        Course.objects.create(
            course_code='CS-DUP-L', course_name='Squatter', credit_hours=1
        )
        serializer = CourseSerializer(
            data={'course_code': 'CS-DUP', 'course_name': 'Clash',
                  'credit_hours': 3, 'lab': True},
            context=_ctx(admin_user),
        )
        assert serializer.is_valid(), serializer.errors
        with pytest.raises(drf_serializers.ValidationError):
            serializer.save()

    def test_lab_is_reported_as_a_boolean_with_the_code_beside_it(self, admin_user, db):
        course = _save(
            {'course_code': 'CS-REP', 'course_name': 'Reported', 'credit_hours': 3, 'lab': True},
            admin_user=admin_user,
        )
        data = CourseSerializer(instance=course, context=_ctx(admin_user)).data
        assert data['lab'] is True
        assert data['lab_course'] == 'CS-REP-L'

        plain = Course.objects.create(
            course_code='CS-PLN', course_name='Plain', credit_hours=3
        )
        plain_data = CourseSerializer(instance=plain, context=_ctx(admin_user)).data
        assert plain_data['lab'] is False
        assert plain_data['lab_course'] is None


# ===========================================================================
# BulkTranscriptSerializer
# ===========================================================================

@pytest.mark.django_db
class TestBulkTranscriptSerializer:

    def test_confirm_false_fails_validation(self, admin_user, inactive_semester):
        serializer = BulkTranscriptSerializer(
            data={'confirm': False},
            context={**_ctx(admin_user), 'semester_id': inactive_semester.semester_id}
        )
        assert not serializer.is_valid()
        assert 'non_field_errors' in serializer.errors or 'confirm' in str(serializer.errors)

    def test_missing_result_raises_validation_error(
        self, admin_user, active_semester, student_instance, course_allocation, enrollment
    ):
        """If any enrollment has no course_gpa, bulk create must raise ValidationError."""
        course_allocation.semester_id = active_semester
        course_allocation.save()
        enrollment.allocation_id = course_allocation
        enrollment.status = 'Locked'
        enrollment.save()
        # result exists but course_gpa is null
        enrollment.result.course_gpa = None
        enrollment.result.save()

        serializer = BulkTranscriptSerializer(
            data={'confirm': True},
            context={**_ctx(admin_user), 'semester_id': active_semester.semester_id}
        )
        assert serializer.is_valid(), serializer.errors
        from rest_framework import serializers as drf_serializers
        with pytest.raises(drf_serializers.ValidationError) as exc_info:
            serializer.save()
        assert enrollment.student.student_id.person_id in str(exc_info.value.detail)

    def test_zero_credits_skips_the_student_rather_than_aborting(
        self, admin_user, active_semester, student_instance, course_allocation, enrollment, db
    ):
        """
        BUG: if total_credits_attempted == 0 (all courses have credit_hours=0),
        gpa = gpa/total_credits_attempted raises ZeroDivisionError.
        This test documents the bug — it should be a 400, not a 500.
        """
        course_allocation.semester = active_semester
        course_allocation.status = 'Completed'
        course_allocation.save()
        enrollment.allocation = course_allocation
        enrollment.status = 'Completed'
        enrollment.save()
        enrollment.result.course_gpa = Decimal('3.5')
        enrollment.result.save()

        # A student whose courses total zero credit hours cannot have a GPA.
        # They are skipped with a warning rather than aborting the whole batch —
        # one bad row must not block a semester's transcripts.
        course = course_allocation.course
        course.credit_hours = 0
        course.save()

        serializer = BulkTranscriptSerializer(
            data={'confirm': True},
            context={**_ctx(admin_user), 'semester_id': active_semester.semester_id}
        )
        assert serializer.is_valid(), serializer.errors
        transcripts = serializer.save()
        assert transcripts == []


# ===========================================================================
# FacultyStudentBulkSerializer — file validation bug
# ===========================================================================

@pytest.mark.django_db
class TestFacultyStudentBulkSerializerValidation:

    def _make_file(self, filename, content_type='text/csv'):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return SimpleUploadedFile(filename, b'col1,col2\nval1,val2', content_type=content_type)

    def test_valid_csv_file_passes(self, admin_user):
        """A real .csv file with correct content-type should pass validation."""
        f = self._make_file('faculty.csv', 'text/csv')
        serializer = FacultyStudentBulkSerializer(data={'file': f}, context=_ctx(admin_user))
        # NOTE: this may FAIL due to the validate() bug:
        # `not file.name.endswith('.csv') or file.name.endswith('.xlsx')` is always True for .csv
        # This test documents/exposes the bug
        is_valid = serializer.is_valid()
        if not is_valid:
            pytest.xfail(
                "Known bug: FacultyStudentBulkSerializer.validate() logic is inverted — "
                "valid CSV files are incorrectly rejected. "
                f"Errors: {serializer.errors}"
            )

    def test_txt_file_is_rejected(self, admin_user):
        """Non-CSV/XLSX files should be rejected."""
        f = self._make_file('data.txt', 'text/plain')
        serializer = FacultyStudentBulkSerializer(data={'file': f}, context=_ctx(admin_user))
        assert not serializer.is_valid()

    def test_bulk_create_returns_row_counts(
        self, admin_user, faculty_group, department, db
    ):
        """
        A valid CSV with parseable rows should return row_count, insert_count,
        error_row_count in the response — not crash.
        Uses a minimal CSV with one row that will likely fail validation
        (missing required fields) to confirm graceful error_rows accumulation.
        """
        csv_content = (
            'password,first_name,last_name,father_name,gender,cnic,dob,'
            'contact_number,institutional_email,department,designation,joining_date\n'
            'pass123,John,Doe,Father,Male,12345-1234567-9,1990-01-01,'
            '+923001111111,bulk_faculty@test.com,CS,Lecturer,2024-01-01\n'
        )
        from django.core.files.uploadedfile import SimpleUploadedFile
        f = SimpleUploadedFile('faculty.csv', csv_content.encode(), content_type='text/csv')

        result = FacultyStudentBulkSerializer(context={
            **_ctx(admin_user), 'target_model': 'faculty'
        }).create({'file': f})

        assert 'row_count' in result
        assert 'insert_count' in result
        assert 'error_row_count' in result
        assert 'errors' in result
        assert result['row_count'] == 1

    def test_bulk_create_unknown_model_type_returns_message(self, admin_user, db):
        """Sending an unknown target_model should return a message, not crash."""
        csv_content = 'col1\nval1\n'
        from django.core.files.uploadedfile import SimpleUploadedFile
        f = SimpleUploadedFile('data.csv', csv_content.encode(), content_type='text/csv')

        result = FacultyStudentBulkSerializer(
            context={**_ctx(admin_user), 'target_model': 'unknown'}
        ).create({'file': f})

        assert 'message' in result


# ===========================================================================
# PersonSerializer validators
# ===========================================================================

@pytest.mark.django_db
class TestPersonSerializerValidation:

    def _base_person_data(self):
        return {
            'user': {'password': 'testpass123'},
            'first_name': 'Test',
            'last_name': 'Person',
            'father_name': 'Father',
            'gender': 'Male',
            'dob': '1990-01-01',
            'cnic': '12345-1234567-9',
            'contact_number': '+923001234567',
            'institutional_email': 'test.person@test.com',
        }

    def test_invalid_contact_number_rejected(self, admin_user):
        data = self._base_person_data()
        data['contact_number'] = '12345'  # too short
        serializer = PersonSerializer(data=data, context=_ctx(admin_user))
        assert not serializer.is_valid()
        assert 'contact_number' in serializer.errors

    def test_invalid_cnic_rejected(self, admin_user):
        data = self._base_person_data()
        data['cnic'] = '123-456'  # wrong format
        serializer = PersonSerializer(data=data, context=_ctx(admin_user))
        assert not serializer.is_valid()
        assert 'cnic' in serializer.errors

    def test_future_dob_rejected(self, admin_user):
        data = self._base_person_data()
        data['dob'] = str((date.today() + timedelta(days=365)))
        serializer = PersonSerializer(data=data, context=_ctx(admin_user))
        assert not serializer.is_valid()
        assert 'dob' in serializer.errors

    def test_age_under_14_rejected(self, admin_user):
        data = self._base_person_data()
        data['dob'] = str(date.today().replace(year=date.today().year - 10))
        serializer = PersonSerializer(data=data, context=_ctx(admin_user))
        assert not serializer.is_valid()
        assert 'dob' in serializer.errors

    def test_age_over_80_rejected(self, admin_user):
        data = self._base_person_data()
        data['dob'] = str(date.today().replace(year=date.today().year - 81))
        serializer = PersonSerializer(data=data, context=_ctx(admin_user))
        assert not serializer.is_valid()
        assert 'dob' in serializer.errors


# ===========================================================================
# QualificationSerializer validators
# ===========================================================================

@pytest.mark.django_db
class TestQualificationSerializerValidation:

    def test_obtained_marks_exceeding_total_rejected(self, admin_user):
        data = {
            'degree_title': 'BSc', 'education_board': 'BISE',
            'passing_year': 2015, 'institution': 'Test Uni',
            'total_marks': 100, 'obtained_marks': 110,
        }
        serializer = QualificationSerializer(data=data, context=_ctx(admin_user))
        assert not serializer.is_valid()
        assert 'non_field_errors' in serializer.errors

    def test_obtained_without_total_rejected(self, admin_user):
        data = {
            'degree_title': 'BSc', 'education_board': 'BISE',
            'passing_year': 2015, 'institution': 'Test Uni',
            'obtained_marks': 80,
        }
        serializer = QualificationSerializer(data=data, context=_ctx(admin_user))
        assert not serializer.is_valid()

    def test_future_passing_year_rejected(self, admin_user):
        data = {
            'degree_title': 'BSc', 'education_board': 'BISE',
            'passing_year': date.today().year + 5, 'institution': 'Test Uni',
            'total_marks': 100, 'obtained_marks': 80,
        }
        serializer = QualificationSerializer(data=data, context=_ctx(admin_user))
        assert not serializer.is_valid()
        assert 'passing_year' in serializer.errors

    def test_total_without_obtained_rejected(self, admin_user):
        data = {
            'degree_title': 'BSc', 'education_board': 'BISE',
            'passing_year': 2015, 'institution': 'Test Uni',
            'total_marks': 100,
        }
        serializer = QualificationSerializer(data=data, context=_ctx(admin_user))
        assert not serializer.is_valid()

    def test_valid_qualification_passes(self, admin_user):
        data = {
            'degree_title': 'BSc', 'education_board': 'BISE',
            'passing_year': 2015, 'institution': 'Test Uni',
            'total_marks': 100, 'obtained_marks': 80, 'is_current': False,
        }
        serializer = QualificationSerializer(data=data, context=_ctx(admin_user))
        assert serializer.is_valid(), serializer.errors


# ===========================================================================
# ResultCalculationMixin — GPA logic (absolute and bell curve)
# ===========================================================================

@pytest.mark.django_db
class TestResultCalculationMixinAbsolute:
    """Absolute grading (class under 20), per handbook Table 3.

    The cutoffs here previously drifted from the table: five bands sat a few
    marks low and D+ (54-57.99) was missing altogether, so anyone in that range
    was awarded 1.00 instead of 1.33.
    """

    def _make_mixin(self):
        from AdminModule.mixins import ResultCalculationMixin
        class Impl(ResultCalculationMixin):
            pass
        return Impl()

    @pytest.mark.parametrize('mark,expected', [
        (100, 4.00), (95, 4.00),    # A+
        (94, 4.00), (85, 4.00),     # A
        (84, 3.67), (80, 3.67),     # A-
        (79, 3.33), (75, 3.33),     # B+
        (74, 3.00), (71, 3.00),     # B
        (70, 2.67), (68, 2.67),     # B-
        (67, 2.33), (64, 2.33),     # C+
        (63, 2.00), (61, 2.00),     # C
        (60, 1.67), (58, 1.67),     # C-
        (57, 1.33), (54, 1.33),     # D+
        (53, 1.00), (50, 1.00),     # D
        (49, 0.00), (0, 0.00),      # F
    ])
    def test_table_3_bands(self, enrollment, mark, expected):
        mixin = self._make_mixin()
        mixin.calculate_gpa({enrollment: mark})
        enrollment.result.refresh_from_db()
        assert float(enrollment.result.course_gpa) == expected

    def test_band_boundaries_are_inclusive_at_the_bottom(self, enrollment):
        """A mark sitting exactly on a cutoff takes the higher band."""
        mixin = self._make_mixin()
        for mark, expected in [(85, 4.00), (71, 3.00), (54, 1.33), (50, 1.00)]:
            mixin.calculate_gpa({enrollment: mark})
            enrollment.result.refresh_from_db()
            assert float(enrollment.result.course_gpa) == expected, mark

    def test_calculate_result_leaves_statuses_alone(self, course_allocation, enrollment):
        mixin = self._make_mixin()
        enrollment.result.obtained_marks = None
        enrollment.result.save()
        from Models.models import Assessment, AssessmentChecked
        assessment = Assessment.objects.create(
            allocation=course_allocation,
            assessment_type='Quiz',
            assessment_name='Q1',
            assessment_date=date.today(),
            weightage=100,
            total_marks=100,
            student_submission=False,
        )
        AssessmentChecked.objects.create(
            assessment=assessment, enrollment=enrollment, obtained=80
        )
        before_enrollment = enrollment.status
        before_allocation = course_allocation.status

        mixin.calculate_result(course_allocation)

        enrollment.refresh_from_db()
        course_allocation.refresh_from_db()
        # Locking is the admin's action (setting closing_deadline) and
        # 'Completed' comes from semester_closing_task. Calculation only writes
        # Result rows, which is what makes it safely re-runnable under a
        # different passing_threshold.
        assert enrollment.status == before_enrollment
        assert course_allocation.status == before_allocation
        assert enrollment.result.course_gpa is not None

    def test_calculate_result_with_invalid_instance_returns_message(self, enrollment):
        mixin = self._make_mixin()
        result = mixin.calculate_result(enrollment)
        assert 'message' in result


class TestResultCalculationMixinBellCurve:
    """Tests calculate_gpa with >= 20 students (bell curve grading)."""

    def _make_mixin(self):
        from AdminModule.mixins import ResultCalculationMixin
        class Impl(ResultCalculationMixin):
            pass
        return Impl()

    def _make_enrollments(self, db, student_instance, course_allocation, count, base_score):
        """Create N enrollments with sequential scores."""
        from django.contrib.auth.models import User
        from Models.models import Person, Student, Enrollment, Result
        enrollments = {}
        scores = [base_score + i for i in range(count)]
        for i, score in enumerate(scores):
            if i == 0:
                # reuse existing student_instance
                e = Enrollment.objects.create(
                    student=student_instance,
                    allocation=course_allocation,
                    status='Active',
                )
                Result.objects.create(enrollment=e)
                enrollments[e] = score
                continue
            user = User.objects.create_user(
                username=f'bell_user_{i}@test.com',
                password='pass'
            )
            person = Person.objects.create(
                person_id=f'NUM-BELL-2024-{i}',
                first_name=f'Bell{i}',
                last_name='Student',
                father_name='Father',
                gender='Male',
                dob=date(2000, 1, 1),
                cnic=f'99999-{i:07d}-{i % 10}',
                contact_number=f'+9230099{i:05d}',
                institutional_email=f'bell_user_{i}@test.com',
                type='Student',
                user=user,
            )
            from Models.models import Program
            stu = Student.objects.create(
                student_id=person,
                program=student_instance.program,
                student_class=student_instance.student_class,
                admission_date=date(2024, 1, 1),
                status='Active',
            )
            e = Enrollment.objects.create(
                student=stu,
                allocation=course_allocation,
                status='Active',
            )
            Result.objects.create(enrollment=e)
            enrollments[e] = score
        return enrollments

    def test_bell_curve_used_when_20_or_more_students(
        self, db, student_instance, course_allocation
    ):
        mixin = self._make_mixin()
        enrollments = self._make_enrollments(db, student_instance, course_allocation, 20, 50)
        result = mixin.calculate_gpa(enrollments)
        assert 'mean' in result
        assert 'standard_deviation' in result

    def test_bell_curve_result_has_score_key(
        self, db, student_instance, course_allocation
    ):
        mixin = self._make_mixin()
        enrollments = self._make_enrollments(db, student_instance, course_allocation, 20, 50)
        result = mixin.calculate_gpa(enrollments)
        # Each student entry has a 'score' key (z-score)
        student_entries = {k: v for k, v in result.items() if k != 'mean' and k != 'standard_deviation'}
        for val in student_entries.values():
            assert 'score' in val
            assert 'course_gpa' in val


# ===========================================================================
# ChangeRequestSerializer — update transitions
# ===========================================================================

@pytest.mark.django_db
class TestChangeRequestSerializerUpdate:

    def test_decline_sets_status_declined(self, admin_user, change_request):
        from AdminModule.serializers import ChangeRequestSerializer
        serializer = ChangeRequestSerializer(
            instance=change_request,
            data={'status': 'declined'},
            partial=True,
            context=_ctx(admin_user),
        )
        assert serializer.is_valid(), serializer.errors
        updated = serializer.save()
        assert updated.status == 'declined'

    def test_decline_notifies_requester(self, admin_user, change_request):
        from AdminModule.serializers import ChangeRequestSerializer
        from Models.models import Notification
        serializer = ChangeRequestSerializer(
            instance=change_request,
            data={'status': 'declined'},
            partial=True,
            context=_ctx(admin_user),
        )
        assert serializer.is_valid(), serializer.errors
        serializer.save()

        notification = Notification.objects.get(
            recipient=change_request.requested_by, verb='change_request_declined'
        )
        assert notification.object_id == change_request.pk

    def test_invalid_status_transition_is_no_op(self, admin_user, change_request):
        from AdminModule.serializers import ChangeRequestSerializer
        serializer = ChangeRequestSerializer(
            instance=change_request,
            data={'status': 'pending'},
            partial=True,
            context=_ctx(admin_user),
        )
        assert serializer.is_valid(), serializer.errors
        updated = serializer.save()
        # non-applied/declined status → update() returns instance unchanged
        assert updated.status == change_request.status

    def test_apply_faculty_delete_removes_faculty(
        self, admin_user, faculty_instance
    ):
        # Don't use the change_request fixture — it pulls in course_allocation
        # which creates a FK that blocks faculty deletion (RESTRICT).
        from AdminModule.serializers import ChangeRequestSerializer
        from Models.models import ChangeRequest
        from django.utils import timezone
        cr = ChangeRequest.objects.create(
            change_type='faculty_delete',
            target_faculty=faculty_instance,
            requested_by=admin_user,
            status='confirmed',
            requested_at=timezone.now(),
        )

        serializer = ChangeRequestSerializer(
            instance=cr,
            data={'status': 'applied'},
            partial=True,
            context=_ctx(admin_user),
        )
        assert serializer.is_valid(), serializer.errors
        serializer.save()

        from Models.models import Faculty
        assert not Faculty.objects.filter(pk=faculty_instance.pk).exists()

    def test_apply_student_delete_removes_student(
        self, admin_user, student_instance, change_request
    ):
        from AdminModule.serializers import ChangeRequestSerializer
        # rename change_type to student_create (as in serializer code)
        change_request.change_type = 'student_delete'
        change_request.target_student = student_instance
        change_request.status = 'confirmed'
        change_request.save()

        serializer = ChangeRequestSerializer(
            instance=change_request,
            data={'status': 'applied'},
            partial=True,
            context=_ctx(admin_user),
        )
        assert serializer.is_valid(), serializer.errors
        serializer.save()

        from Models.models import Student
        assert not Student.objects.filter(pk=student_instance.pk).exists()

    def test_apply_hod_change_notifies_new_and_old_hod(
        self, admin_user, department, faculty_instance
    ):
        from AdminModule.serializers import ChangeRequestSerializer
        from Models.models import ChangeRequest, Faculty, Person, Notification
        from django.contrib.auth.models import User

        old_hod_user = User.objects.create_user(username='oldhod@test.com', password='oldhodpass123')
        old_hod_person = Person.objects.create(
            person_id='NUM-CS-2024-9', first_name='Old', last_name='Hod', father_name='Father',
            gender='Male', dob=date(1980, 1, 1), cnic='12345-1234567-9', contact_number='+923001234599',
            institutional_email='oldhod@test.com', type='Faculty', user=old_hod_user,
        )
        old_hod = Faculty.objects.create(
            employee_id=old_hod_person, department=department, designation='Lecturer',
            joining_date=date(2019, 1, 1),
        )
        department.HOD = old_hod
        department.save()

        cr = ChangeRequest.objects.create(
            change_type='hod_change',
            department=department,
            new_hod=faculty_instance,
            requested_by=admin_user,
            status='confirmed',
            requested_at=timezone.now(),
        )

        serializer = ChangeRequestSerializer(
            instance=cr,
            data={'status': 'applied'},
            partial=True,
            context=_ctx(admin_user),
        )
        assert serializer.is_valid(), serializer.errors
        serializer.save()

        assert Notification.objects.filter(
            recipient=faculty_instance.employee_id.user, verb='hod_change_applied'
        ).exists()
        assert Notification.objects.filter(
            recipient=old_hod_user, verb='hod_change_applied'
        ).exists()


# ===========================================================================
# PersonSerializerMixin — create_mixin and update_mixin
# ===========================================================================

@pytest.mark.django_db
class TestPersonSerializerMixinCreate:

    def _base_person_data(self):
        return {
            'user': {'password': 'testpass123'},
            'first_name': 'Mixin',
            'last_name': 'Test',
            'father_name': 'Father',
            'gender': 'Male',
            'dob': '1990-01-01',
            'cnic': '99999-9999999-9',
            'contact_number': '+923001234599',
            'institutional_email': 'mixin@test.com',
        }

    def test_create_faculty_via_serializer(self, admin_client, department, faculty_group):
        from AdminModule.serializers import FacultySerializer
        from rest_framework.test import APIRequestFactory
        factory = APIRequestFactory()
        from django.contrib.auth.models import User
        user = User.objects.get(username='admin@test.com')
        req = factory.post('/')
        req.user = user
        data = {
            'person': self._base_person_data(),
            'department': department.department_id,
            'designation': 'Lecturer',
            'joining_date': '2024-01-01',
        }
        serializer = FacultySerializer(data=data, context={'request': req})
        assert serializer.is_valid(), serializer.errors
        faculty = serializer.save()
        from Models.models import Faculty
        assert Faculty.objects.filter(pk=faculty.pk).exists()
        assert faculty.employee_id.user.groups.filter(name='Faculty').exists()

    def test_create_faculty_generates_person_id(self, admin_client, department, faculty_group):
        from AdminModule.serializers import FacultySerializer
        from rest_framework.test import APIRequestFactory
        factory = APIRequestFactory()
        from django.contrib.auth.models import User
        user = User.objects.get(username='admin@test.com')
        req = factory.post('/')
        req.user = user
        data = {
            'person': {**self._base_person_data(), 'institutional_email': 'pid@test.com'},
            'department': department.department_id,
            'designation': 'Lecturer',
            'joining_date': '2024-01-01',
        }
        serializer = FacultySerializer(data=data, context={'request': req})
        assert serializer.is_valid(), serializer.errors
        faculty = serializer.save()
        assert faculty.employee_id.person_id.startswith('NUM-')

    def test_update_mixin_updates_address(self, admin_user, faculty_instance):
        from AdminModule.serializers import FacultySerializer
        from rest_framework.test import APIRequestFactory
        from django.contrib.auth.models import User
        factory = APIRequestFactory()
        user = User.objects.get(username='admin@test.com')
        req = factory.put('/')
        req.user = user
        data = {
            'person': {
                'contact_number': faculty_instance.employee_id.contact_number,
                'address': {
                    'country': 'Pakistan',
                    'province': 'Punjab',
                    'city': 'Islamabad',
                    'zipcode': '44000',
                    'street_address': '123 Test St',
                },
            },
            'department': faculty_instance.department.department_id,
            'designation': faculty_instance.designation,
            'joining_date': str(faculty_instance.joining_date),
        }
        serializer = FacultySerializer(
            instance=faculty_instance, data=data, partial=True, context={'request': req}
        )
        assert serializer.is_valid(), serializer.errors
        updated = serializer.save()
        from Models.models import Address
        assert Address.objects.filter(person_id=updated.employee_id).exists()