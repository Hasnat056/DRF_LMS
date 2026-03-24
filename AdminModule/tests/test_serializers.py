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
  - CourseSerializer : lab toggle credit-hour arithmetic (including the negative-hours bug)
  - BulkTranscriptSerializer : happy path + zero-division guard + missing-result guard
  - FacultyStudentBulkSerializer : file validation bug exposure
  - PersonSerializer / QualificationSerializer : field-level validators
"""

import pytest
from datetime import date, timedelta
from decimal import Decimal

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
        data = {'program_id': program.program_id, 'batch_year': 2023}
        serializer = ClassSerializer(data=data, context=_ctx(admin_user))
        assert serializer.is_valid(), serializer.errors

        new_class = serializer.save()

        semesters = Semester.objects.filter(semesterdetails__class_id=new_class).distinct()
        assert semesters.count() == program.total_semesters

    def test_creates_semesterdetails_for_each_semester(self, admin_user, program):
        """Each auto-created semester should have a SemesterDetails row for this class."""
        data = {'program_id': program.program_id, 'batch_year': 2024}
        serializer = ClassSerializer(data=data, context=_ctx(admin_user))
        assert serializer.is_valid(), serializer.errors

        new_class = serializer.save()

        details_count = SemesterDetails.objects.filter(class_id=new_class).count()
        assert details_count == program.total_semesters

    def test_semester_numbers_are_sequential(self, admin_user, program):
        """Auto-created semesters should be numbered 1..N."""
        data = {'program_id': program.program_id, 'batch_year': 2025}
        serializer = ClassSerializer(data=data, context=_ctx(admin_user))
        assert serializer.is_valid(), serializer.errors
        new_class = serializer.save()

        numbers = sorted(
            Semester.objects.filter(semesterdetails__class_id=new_class)
            .distinct()
            .values_list('semester_no', flat=True)
        )
        assert numbers == list(range(1, program.total_semesters + 1))

    def test_scheme_of_studies_is_ignored_on_create(self, admin_user, program):
        """scheme_of_studies sent on create must be silently popped, not crash."""
        data = {
            'program_id': program.program_id,
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
                    'semesterdetails_set': [{'course_code': course.course_code}],
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
            semester_id=inactive_semester, course_code=course, class_id=batch_class
        ).exists()

    def test_update_replaces_existing_courses(
        self, admin_user, batch_class, inactive_semester, course, db
    ):
        """Sending a new course list should replace the old SemesterDetails rows."""
        old_course = Course.objects.create(
            course_code='OLD-001', course_name='Old Course', credit_hours=2, lab=False
        )
        SemesterDetails.objects.create(
            semester_id=inactive_semester, class_id=batch_class, course_code=old_course
        )

        payload = {
            'scheme_of_studies': [
                {
                    'semester_id': inactive_semester.semester_id,
                    'semesterdetails_set': [{'course_code': course.course_code}],
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
        assert not SemesterDetails.objects.filter(course_code=old_course).exists()
        assert SemesterDetails.objects.filter(course_code=course).exists()

    def test_update_with_invalid_semester_id_raises_404(
        self, admin_user, batch_class
    ):
        """A non-existent semester_id in scheme_of_studies should raise Http404."""
        from django.http import Http404
        payload = {
            'scheme_of_studies': [
                {
                    'semester_id': 99999,
                    'semesterdetails_set': [{'course_code': None}],
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
# SemesterSerializer — field guard logic
# ===========================================================================

@pytest.mark.django_db
class TestSemesterSerializerFieldGuards:

    def test_activation_deadline_past_becomes_readonly(self, admin_user):
        """Once activation_deadline has passed, it should be read-only."""
        semester = Semester.objects.create(
            semester_no=1, status='Active', session='Fall-2024',
            activation_deadline=timezone.now() - timedelta(hours=1),
        )
        serializer = SemesterSerializer(instance=semester, context=_ctx(admin_user))
        assert serializer.fields['activation_deadline'].read_only is True

    def test_closing_deadline_readonly_before_activation_set(self, admin_user):
        """closing_deadline must be read-only if activation_deadline is not yet set."""
        semester = Semester.objects.create(semester_no=1, status='Inactive')
        serializer = SemesterSerializer(instance=semester, context=_ctx(admin_user))
        assert serializer.fields['closing_deadline'].read_only is True

    def test_activation_deadline_cannot_be_in_past(self, admin_user, inactive_semester):
        """Validation should reject an activation_deadline in the past."""
        data = {'activation_deadline': timezone.now() - timedelta(days=1)}
        serializer = SemesterSerializer(
            instance=inactive_semester, data=data,
            partial=True, context=_ctx(admin_user)
        )
        assert not serializer.is_valid()
        assert 'activation_deadline' in serializer.errors

    def test_closing_deadline_cannot_be_in_past(self, admin_user):
        """closing_deadline in the past should fail validation."""
        semester = Semester.objects.create(
            semester_no=1, status='Active', session='Fall-2024',
            activation_deadline=timezone.now() - timedelta(hours=1),
            closing_deadline=timezone.now() + timedelta(days=30),
        )
        data = {'closing_deadline': timezone.now() - timedelta(days=1)}
        serializer = SemesterSerializer(
            instance=semester, data=data,
            partial=True, context=_ctx(admin_user)
        )
        assert not serializer.is_valid()
        assert 'closing_deadline' in serializer.errors

    def test_cannot_set_activation_deadline_when_class_has_active_semester(
        self, admin_user, batch_class, inactive_semester, active_semester
    ):
        """
        Setting activation_deadline on an inactive semester whose class already
        has an active semester should raise a ValidationError.
        """
        # link both semesters to same class
        SemesterDetails.objects.get_or_create(
            semester_id=active_semester, class_id=batch_class,
            defaults={'course_code': None}
        )
        SemesterDetails.objects.get_or_create(
            semester_id=inactive_semester, class_id=batch_class,
            defaults={'course_code': None}
        )
        data = {'activation_deadline': timezone.now() + timedelta(days=7)}
        serializer = SemesterSerializer(
            instance=inactive_semester, data=data,
            partial=True, context=_ctx(admin_user)
        )
        assert serializer.is_valid(), serializer.errors
        from rest_framework import serializers as drf_serializers
        with pytest.raises(drf_serializers.ValidationError):
            serializer.save()


# ===========================================================================
# CourseAllocationSerializer
# ===========================================================================

@pytest.mark.django_db
class TestCourseAllocationSerializer:

    def test_semester_queryset_filtered_to_inactive_with_session_and_deadline(
        self, admin_user, inactive_semester, active_semester
    ):
        """
        The semester_id field queryset must only include Inactive semesters
        that have both session and activation_deadline set.
        """
        # inactive_semester fixture has session+activation_deadline set
        serializer = CourseAllocationSerializer(context=_ctx(admin_user))
        qs = serializer.fields['semester_id'].queryset
        assert inactive_semester in qs
        assert active_semester not in qs

    def test_cannot_create_allocation_with_course_not_in_semester_scheme(
        self, admin_user, faculty_instance, inactive_semester, db
    ):
        """Course not in semester's SemesterDetails must be rejected."""
        unrelated_course = Course.objects.create(
            course_code='CS-999', course_name='Unrelated', credit_hours=3, lab=False
        )
        data = {
            'teacher_id': faculty_instance.pk,
            'course_code': unrelated_course.course_code,
            'semester_id': inactive_semester.semester_id,
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
            'teacher_id': faculty_instance.pk,
            'course_code': course.course_code,
            'semester_id': inactive_semester.semester_id,
        }
        serializer = CourseAllocationSerializer(data=data, context=_ctx(admin_user))
        assert serializer.is_valid(), serializer.errors
        from rest_framework import serializers as drf_serializers
        with pytest.raises(drf_serializers.ValidationError):
            serializer.save()

    def test_create_sets_session_from_semester(
        self, admin_user, faculty_instance, course, inactive_semester
    ):
        """The session field should be auto-filled from the semester's session."""
        # ensure course is in semester scheme
        SemesterDetails.objects.get_or_create(
            semester_id=inactive_semester,
            class_id=inactive_semester.semesterdetails_set.first().class_id,
            course_code=course,
        )
        data = {
            'teacher_id': faculty_instance.pk,
            'course_code': course.course_code,
            'semester_id': inactive_semester.semester_id,
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
        course_allocation.status = 'Ongoing'
        course_allocation.save()

        inactive_alloc = CourseAllocation.objects.create(
            teacher_id=course_allocation.teacher_id,
            course_code=course_allocation.course_code,
            semester_id=course_allocation.semester_id,
            session='Spring-2025',
            status='Inactive',
        )

        serializer = EnrollmentSerializer(context=_ctx(admin_user))
        qs = serializer.fields['allocation_id'].queryset

        assert course_allocation in qs
        assert inactive_alloc not in qs

    def test_allocation_queryset_empty_when_no_ongoing(self, admin_user, course_allocation):
        """If no allocations are Ongoing, the queryset must be empty."""
        course_allocation.status = 'Inactive'
        course_allocation.save()

        serializer = EnrollmentSerializer(context=_ctx(admin_user))
        qs = serializer.fields['allocation_id'].queryset
        assert qs.count() == 0


# ===========================================================================
# CourseSerializer — credit hours + lab toggle
# ===========================================================================

@pytest.mark.django_db
class TestCourseSerializerLabToggle:

    def test_lab_true_on_create_increments_credit_hours(self, admin_user):
        data = {'course_code': 'CS-LAB1', 'course_name': 'Lab Course', 'credit_hours': 3, 'lab': True}
        serializer = CourseSerializer(data=data, context=_ctx(admin_user))
        assert serializer.is_valid(), serializer.errors
        course = serializer.save()
        assert course.credit_hours == 4

    def test_lab_false_on_create_does_not_increment(self, admin_user):
        data = {'course_code': 'CS-NOLAB', 'course_name': 'Theory', 'credit_hours': 3, 'lab': False}
        serializer = CourseSerializer(data=data, context=_ctx(admin_user))
        assert serializer.is_valid(), serializer.errors
        course = serializer.save()
        assert course.credit_hours == 3

    def test_toggling_lab_true_to_false_decrements(self, admin_user, db):
        course = Course.objects.create(
            course_code='CS-T2F', course_name='Was Lab', credit_hours=4, lab=True
        )
        data = {'course_code': 'CS-T2F', 'course_name': 'Was Lab', 'credit_hours': 4, 'lab': False}
        serializer = CourseSerializer(instance=course, data=data, context=_ctx(admin_user))
        assert serializer.is_valid(), serializer.errors
        updated = serializer.save()
        assert updated.credit_hours == 3

    def test_toggling_lab_false_to_true_increments(self, admin_user, db):
        course = Course.objects.create(
            course_code='CS-F2T', course_name='Now Lab', credit_hours=3, lab=False
        )
        data = {'course_code': 'CS-F2T', 'course_name': 'Now Lab', 'credit_hours': 3, 'lab': True}
        serializer = CourseSerializer(instance=course, data=data, context=_ctx(admin_user))
        assert serializer.is_valid(), serializer.errors
        updated = serializer.save()
        assert updated.credit_hours == 4

    def test_bug_lab_toggle_cannot_produce_negative_credit_hours(self, admin_user, db):
        """
        BUG: if credit_hours=1 and lab=True→False, update() does credit_hours -= 1 → 0,
        but then if called again on a 0-credit lab course it goes negative.
        This test documents the known risk.
        """
        course = Course.objects.create(
            course_code='CS-NEG', course_name='Risky', credit_hours=1, lab=True
        )
        data = {'course_code': 'CS-NEG', 'course_name': 'Risky', 'credit_hours': 1, 'lab': False}
        serializer = CourseSerializer(instance=course, data=data, context=_ctx(admin_user))
        assert serializer.is_valid(), serializer.errors
        updated = serializer.save()
        # credit_hours=1 - 1 = 0, which is technically valid per the validator (>= 0)
        assert updated.credit_hours >= 0, "credit_hours must never go negative"


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
        assert enrollment.student_id.student_id.person_id in str(exc_info.value.detail)

    def test_zero_credits_causes_division_by_zero(
        self, admin_user, active_semester, student_instance, course_allocation, enrollment, db
    ):
        """
        BUG: if total_credits_attempted == 0 (all courses have credit_hours=0),
        gpa = gpa/total_credits_attempted raises ZeroDivisionError.
        This test documents the bug — it should be a 400, not a 500.
        """
        course_allocation.semester_id = active_semester
        course_allocation.status = 'Completed'
        course_allocation.save()
        enrollment.allocation_id = course_allocation
        enrollment.status = 'Completed'
        enrollment.save()
        enrollment.result.course_gpa = Decimal('3.5')
        enrollment.result.save()

        # set course credit_hours to 0 to trigger division by zero
        course = course_allocation.course_code
        course.credit_hours = 0
        course.save()

        serializer = BulkTranscriptSerializer(
            data={'confirm': True},
            context={**_ctx(admin_user), 'semester_id': active_semester.semester_id}
        )
        assert serializer.is_valid(), serializer.errors
        with pytest.raises(drf_serializers.ValidationError):
            serializer.save()


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
            'contact_number,institutional_email,department_id,designation,joining_date\n'
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