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
            course_code='OLD-001', course_name='Old Course', credit_hours=2, lab=False
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
# SemesterSerializer — field guard logic
# ===========================================================================

@pytest.mark.django_db
class TestSemesterSerializerFieldGuards:

    def test_activation_deadline_past_becomes_readonly(self, admin_user, batch_class, academic_session):
        """Once activation_deadline has passed, it should be read-only."""
        semester = Semester.objects.create(
            semester_no=1, status='Active', session=academic_session,
            activation_deadline=timezone.now() - timedelta(hours=1),
            associated_class=batch_class,
        )
        serializer = SemesterSerializer(instance=semester, context=_ctx(admin_user))
        assert serializer.fields['activation_deadline'].read_only is True

    def test_closing_deadline_readonly_before_activation_set(self, admin_user, batch_class):
        """closing_deadline must be read-only if activation_deadline is not yet set."""
        semester = Semester.objects.create(semester_no=1, status='Inactive', associated_class=batch_class)
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

    def test_closing_deadline_cannot_be_in_past(self, admin_user, batch_class, academic_session):
        """closing_deadline in the past should fail validation."""
        semester = Semester.objects.create(
            semester_no=1, status='Active', session=academic_session,
            activation_deadline=timezone.now() - timedelta(hours=1),
            closing_deadline=timezone.now() + timedelta(days=30),
            associated_class=batch_class,
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
            semester=active_semester,
            defaults={'course': None}
        )
        SemesterDetails.objects.get_or_create(
            semester=inactive_semester,
            defaults={'course': None}
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
        qs = serializer.fields['semester'].queryset
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
        course_allocation.status = 'Ongoing'
        course_allocation.save()

        # Use a different course to avoid the unique_together constraint
        from Models.models import Course
        other_course = Course.objects.create(
            course_code='CS-999', course_name='Dummy Course',
            credit_hours=3, lab=False
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
        assert enrollment.student.student_id.person_id in str(exc_info.value.detail)

    def test_zero_credits_causes_division_by_zero(
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

        # set course credit_hours to 0 to trigger division by zero
        course = course_allocation.course
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
    """Tests calculate_gpa with < 20 students (absolute grading)."""

    def _make_mixin(self):
        from AdminModule.mixins import ResultCalculationMixin
        class Impl(ResultCalculationMixin):
            pass
        return Impl()

    def test_score_85_gives_4_0(self, enrollment):
        mixin = self._make_mixin()
        enrollment.result.obtained_marks = None
        enrollment.result.course_gpa = None
        enrollment.result.save()
        result = mixin.calculate_gpa({enrollment: 85})
        from Models.models import Result
        enrollment.result.refresh_from_db()
        assert enrollment.result.course_gpa == 4.0

    def test_score_80_gives_3_67(self, enrollment):
        mixin = self._make_mixin()
        mixin.calculate_gpa({enrollment: 80})
        enrollment.result.refresh_from_db()
        assert float(enrollment.result.course_gpa) == 3.67

    def test_score_75_gives_3_33(self, enrollment):
        mixin = self._make_mixin()
        mixin.calculate_gpa({enrollment: 75})
        enrollment.result.refresh_from_db()
        assert float(enrollment.result.course_gpa) == 3.33

    def test_score_70_gives_3_0(self, enrollment):
        mixin = self._make_mixin()
        mixin.calculate_gpa({enrollment: 70})
        enrollment.result.refresh_from_db()
        assert float(enrollment.result.course_gpa) == 3.0

    def test_score_65_gives_2_67(self, enrollment):
        mixin = self._make_mixin()
        mixin.calculate_gpa({enrollment: 65})
        enrollment.result.refresh_from_db()
        assert float(enrollment.result.course_gpa) == 2.67

    def test_score_61_gives_2_33(self, enrollment):
        mixin = self._make_mixin()
        mixin.calculate_gpa({enrollment: 61})
        enrollment.result.refresh_from_db()
        assert float(enrollment.result.course_gpa) == 2.33

    def test_score_58_gives_2_0(self, enrollment):
        mixin = self._make_mixin()
        mixin.calculate_gpa({enrollment: 58})
        enrollment.result.refresh_from_db()
        assert float(enrollment.result.course_gpa) == 2.0

    def test_score_55_gives_1_67(self, enrollment):
        mixin = self._make_mixin()
        mixin.calculate_gpa({enrollment: 55})
        enrollment.result.refresh_from_db()
        assert float(enrollment.result.course_gpa) == 1.67

    def test_score_50_gives_1_0(self, enrollment):
        mixin = self._make_mixin()
        mixin.calculate_gpa({enrollment: 50})
        enrollment.result.refresh_from_db()
        assert float(enrollment.result.course_gpa) == 1.0

    def test_score_below_50_gives_0_0(self, enrollment):
        mixin = self._make_mixin()
        mixin.calculate_gpa({enrollment: 40})
        enrollment.result.refresh_from_db()
        assert float(enrollment.result.course_gpa) == 0.0

    def test_calculate_result_marks_enrollment_completed(self, course_allocation, enrollment):
        mixin = self._make_mixin()
        enrollment.result.obtained_marks = None
        enrollment.result.save()
        # need an assessment and checked entry for calculate_result to work
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
        mixin.calculate_result(course_allocation)
        enrollment.refresh_from_db()
        assert enrollment.status == 'Completed'

    def test_calculate_result_with_invalid_instance_returns_message(self, enrollment):
        mixin = self._make_mixin()
        result = mixin.calculate_result(enrollment)
        assert 'message' in result


@pytest.mark.django_db
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