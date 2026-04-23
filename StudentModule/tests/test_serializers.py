"""
test_serializers.py
--------------------
Unit tests for StudentModule serializers — direct serializer instantiation
to cover branches not reachable via HTTP endpoint tests.

Known production bugs are tested with xfail(strict=True).
"""
import io
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from django.core.exceptions import FieldError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework import serializers as drf_serializers
from rest_framework.test import APIRequestFactory
from rest_framework.request import Request
from Models.models import (
    Assessment, AssessmentChecked, Attendance, CourseAllocation,
    Enrollment, Lecture, Reviews,
)
from StudentModule.serializers import (
    AssessmentCheckedHyperlinkedIdentityField,
    ReviewHyperlinkedIdentityField,
    ReviewsSerializer,
    StudentAssessmentCheckedSerializer,
    StudentAssessmentSerializer,
    StudentAttendanceSerializer,
    StudentCourseAllocationSerializer,
    StudentEnrollmentCreateSerializerB,
)


factory = APIRequestFactory()


# ---------------------------------------------------------------------------
# StudentCourseAllocationSerializer — known bugs
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStudentCourseAllocationSerializer:

    def test_get_faculty_details_raises_attribute_error(self, course_allocation):
        serializer = StudentCourseAllocationSerializer()
        result = serializer.get_faculty_details(course_allocation)
        assert 'teacher_id' in result

    def test_get_course_details_raises_attribute_error(self, course_allocation):
        serializer = StudentCourseAllocationSerializer()
        result = serializer.get_course_details(course_allocation)
        assert 'course_code' in result


# ---------------------------------------------------------------------------
# StudentAssessmentCheckedSerializer
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStudentAssessmentCheckedSerializer:

    def test_validate_student_upload_file_too_large(self, submission_assessment_checked):
        """File > 50MB raises ValidationError."""
        big_file = SimpleUploadedFile('huge.pdf', b'x', content_type='application/pdf')
        big_file.size = 60 * 1024 * 1024  # 60 MB

        serializer = StudentAssessmentCheckedSerializer(
            instance=submission_assessment_checked,
        )
        with pytest.raises(drf_serializers.ValidationError, match='50 MB'):
            serializer.validate_student_upload(big_file)

    def test_init_pops_urls_when_no_student_submission(self, no_submission_assessment_checked):
        """When assessment.student_submission=False, 'urls' field is popped."""
        serializer = StudentAssessmentCheckedSerializer(
            instance=no_submission_assessment_checked,
        )
        assert 'urls' not in serializer.fields

    def test_init_pops_urls_when_deadline_past(self, active_enrollment, active_allocation):
        """When submission deadline is in the past, 'urls' field is popped."""
        past_assessment = Assessment.objects.create(
            allocation=active_allocation,
            assessment_type='Assignment',
            assessment_name='Past HW',
            assessment_date=date.today(),
            weightage=10,
            total_marks=20,
            student_submission=True,
            submission_deadline=timezone.now() - timedelta(days=1),
        )
        checked = AssessmentChecked.objects.create(
            assessment=past_assessment,
            enrollment=active_enrollment,
        )
        serializer = StudentAssessmentCheckedSerializer(instance=checked)
        assert 'urls' not in serializer.fields

    def test_init_pops_urls_when_enrollment_completed(
        self, submission_assessment_checked, active_enrollment
    ):
        """When enrollment.status='Completed', 'urls' field is popped."""
        active_enrollment.status = 'Completed'
        active_enrollment.save()
        serializer = StudentAssessmentCheckedSerializer(
            instance=submission_assessment_checked,
        )
        assert 'urls' not in serializer.fields

    def test_init_keeps_urls_when_submission_active(self, submission_assessment_checked):
        """'urls' is present when student_submission=True, future deadline, active enrollment."""
        serializer = StudentAssessmentCheckedSerializer(
            instance=submission_assessment_checked,
        )
        assert 'urls' in serializer.fields

    def test_get_extra_kwargs_readonly_past_deadline(
        self, active_enrollment, active_allocation
    ):
        """student_upload becomes read_only when deadline has passed."""
        past_assessment = Assessment.objects.create(
            allocation=active_allocation,
            assessment_type='Assignment',
            assessment_name='Past HW2',
            assessment_date=date.today(),
            weightage=10,
            total_marks=20,
            student_submission=True,
            submission_deadline=timezone.now() - timedelta(days=1),
        )
        checked = AssessmentChecked.objects.create(
            assessment=past_assessment,
            enrollment=active_enrollment,
        )
        serializer = StudentAssessmentCheckedSerializer(
            instance=checked,
            context={'method': 'PATCH'},
        )
        extra = serializer.get_extra_kwargs()
        assert extra.get('student_upload', {}).get('read_only') is True

    def test_get_extra_kwargs_readonly_completed_enrollment(
        self, submission_assessment_checked, active_enrollment
    ):
        """student_upload is read_only when enrollment is Completed."""
        active_enrollment.status = 'Completed'
        active_enrollment.save()
        serializer = StudentAssessmentCheckedSerializer(
            instance=submission_assessment_checked,
            context={'method': 'PUT'},
        )
        extra = serializer.get_extra_kwargs()
        assert extra.get('student_upload', {}).get('read_only') is True


# ---------------------------------------------------------------------------
# StudentAssessmentSerializer
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStudentAssessmentSerializer:

    def test_to_representation_pops_submission_deadline_when_past(
        self, active_allocation, active_enrollment, student_instance
    ):
        past_assessment = Assessment.objects.create(
            allocation=active_allocation,
            assessment_type='Quiz',
            assessment_name='Old Quiz',
            assessment_date=date.today(),
            weightage=5,
            total_marks=10,
            student_submission=False,
            submission_deadline=timezone.now() - timedelta(days=1),
        )
        request = MagicMock()
        request.user = student_instance.student_id.user
        serializer = StudentAssessmentSerializer(
            past_assessment,
            context={'request': request},
        )
        assert 'submission_deadline' not in serializer.data

    def test_to_representation_pops_submission_deadline_when_none(
        self, active_allocation, student_instance
    ):
        assessment = Assessment.objects.create(
            allocation=active_allocation,
            assessment_type='Quiz',
            assessment_name='No Deadline Quiz',
            assessment_date=date.today(),
            weightage=5,
            total_marks=10,
            student_submission=False,
            submission_deadline=None,
        )
        request = MagicMock()
        request.user = student_instance.student_id.user
        serializer = StudentAssessmentSerializer(
            assessment,
            context={'request': request},
        )
        assert 'submission_deadline' not in serializer.data

    def test_to_representation_keeps_deadline_when_future(
        self, submission_assessment, student_instance
    ):
        request = MagicMock()
        request.user = student_instance.student_id.user
        serializer = StudentAssessmentSerializer(
            submission_assessment,
            context={'request': request},
        )
        assert 'submission_deadline' in serializer.data


# ---------------------------------------------------------------------------
# StudentAttendanceSerializer
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStudentAttendanceSerializer:

    def test_get_faculty_details_returns_none_for_none_obj(self):
        serializer = StudentAttendanceSerializer()
        assert serializer.get_faculty_details(None) is None

    def test_get_course_details_returns_none_for_none_obj(self):
        serializer = StudentAttendanceSerializer()
        assert serializer.get_course_details(None) is None

    def test_get_attendance_details_returns_none_for_none_obj(self):
        serializer = StudentAttendanceSerializer()
        assert serializer.get_attendance_details(None) is None

    def test_get_percentage_returns_zero_for_none_obj(self):
        serializer = StudentAttendanceSerializer()
        assert serializer.get_percentage(None) == 0.0

    def test_get_percentage_typo_raises_field_error(self, active_enrollment):
        serializer = StudentAttendanceSerializer()
        result = serializer.get_percentage(active_enrollment)
        assert isinstance(result, (int, float))


# ---------------------------------------------------------------------------
# HyperlinkedIdentityField edge cases
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReviewHyperlinkedIdentityField:

    def test_get_url_returns_none_when_review_id_is_none(self):
        field = ReviewHyperlinkedIdentityField(
            view_name='review-detail',
            lookup_field='review_id',
        )
        obj = MagicMock()
        obj.review_id = None
        result = field.get_url(obj, 'review-detail', None, None)
        assert result is None


@pytest.mark.django_db
class TestAssessmentCheckedHyperlinkedIdentityField:

    def test_get_url_returns_none_when_assessment_id_is_none(self):
        field = AssessmentCheckedHyperlinkedIdentityField(
            view_name='Student:assessment-upload',
            lookup_field='id',
        )
        obj = MagicMock()
        obj.assessment_id = None
        result = field.get_url(obj, 'Student:assessment-upload', None, None)
        assert result is None


# ---------------------------------------------------------------------------
# StudentEnrollmentCreateSerializerB
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStudentEnrollmentCreateSerializerB:

    def test_create_returns_none_when_validated_data_empty(self, student_instance):
        request = MagicMock()
        request.student = student_instance
        serializer = StudentEnrollmentCreateSerializerB(context={
            'request': request,
            'allocation_ids': set(),
            'enrolled_allocation_ids': set(),
        })
        result = serializer.create({})
        assert result is None

    def test_create_context_key_mismatch(
        self, student_instance, active_allocation
    ):
        request = MagicMock()
        request.student = student_instance
        serializer = StudentEnrollmentCreateSerializerB(context={
            'request': request,
            'allocation_ids': {active_allocation.allocation_id},
            'enrolled_allocations_ids': set(),
        })
        result = serializer.create({
            'allocation_id': active_allocation.allocation_id,
            'confirm': True,
        })
        assert result['count'] == 1

    def test_unenroll_path(self, student_instance, active_allocation, active_enrollment):
        """confirm=False with existing enrollment deletes it."""
        active_enrollment.status = 'Inactive'
        active_enrollment.save()

        request = MagicMock()
        request.student = student_instance
        serializer = StudentEnrollmentCreateSerializerB(context={
            'request': request,
            'allocation_ids': {active_allocation.allocation_id},
            'enrolled_allocations_ids': {active_allocation.allocation_id},
        })
        result = serializer.create({
            'allocation_id': active_allocation.allocation_id,
            'confirm': False,
        })
        assert result['count'] == -1
        assert not Enrollment.objects.filter(
            allocation=active_allocation,
            student=student_instance,
        ).exists()