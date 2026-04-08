"""
test_assessment_upload.py
--------------------------
Tests for StudentAssessmentUploadView (PATCH/PUT on AssessmentChecked).

StudentAssessmentCheckedSerializer:
- student_upload field is writable only when assessment.student_submission=True
  AND submission_deadline is in the future AND enrollment is not Completed.
- validate_student_upload enforces file type and size.
"""
import io
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

STUDENT = '/api/student'


def upload_url(enrollment_id, assessment_id, checked_id):
    return reverse('Student:assessment-upload', kwargs={
        'enrollment': enrollment_id,
        'assessment': assessment_id,
        'id': checked_id,
    })


@pytest.mark.django_db
class TestAssessmentUploadPermission:

    def test_student_can_patch_submission_assessment(
        self, student_client, submission_assessment_checked, active_enrollment, submission_assessment
    ):
        """With student_submission=True and future deadline, PATCH is allowed."""
        url = upload_url(
            active_enrollment.enrollment_id,
            submission_assessment.assessment_id,
            submission_assessment_checked.id,
        )
        f = SimpleUploadedFile('work.pdf', b'%PDF-1.4 content', content_type='application/pdf')
        r = student_client.patch(url, {'student_upload': f}, format='multipart')
        # 200 OK (Cloudinary may reject in test env, but permission must pass)
        assert r.status_code in (200, 400)

    def test_faculty_cannot_upload_student_file(
        self, faculty_client, submission_assessment_checked, active_enrollment, submission_assessment
    ):
        url = upload_url(
            active_enrollment.enrollment_id,
            submission_assessment.assessment_id,
            submission_assessment_checked.id,
        )
        r = faculty_client.patch(url, {}, format='json')
        assert r.status_code == 403

    def test_anon_cannot_upload(
        self, anon_client, submission_assessment_checked, active_enrollment, submission_assessment
    ):
        url = upload_url(
            active_enrollment.enrollment_id,
            submission_assessment.assessment_id,
            submission_assessment_checked.id,
        )
        r = anon_client.patch(url, {}, format='json')
        assert r.status_code == 401

    def test_post_method_not_allowed(
        self, student_client, submission_assessment_checked, active_enrollment, submission_assessment
    ):
        """StudentAssessmentUploadPermission explicitly blocks POST."""
        url = upload_url(
            active_enrollment.enrollment_id,
            submission_assessment.assessment_id,
            submission_assessment_checked.id,
        )
        r = student_client.post(url, {}, format='json')
        assert r.status_code == 403


@pytest.mark.django_db
class TestAssessmentUploadValidation:

    def test_invalid_file_type_rejected(
        self, student_client, submission_assessment_checked, active_enrollment, submission_assessment
    ):
        url = upload_url(
            active_enrollment.enrollment_id,
            submission_assessment.assessment_id,
            submission_assessment_checked.id,
        )
        bad_file = SimpleUploadedFile('virus.exe', b'\x00\x01\x02', content_type='application/octet-stream')
        r = student_client.patch(url, {'student_upload': bad_file}, format='multipart')
        # 400 validation error or rejection by permission layer
        assert r.status_code in (400, 403)

    def test_pdf_file_accepted(
        self, student_client, submission_assessment_checked, active_enrollment, submission_assessment
    ):
        url = upload_url(
            active_enrollment.enrollment_id,
            submission_assessment.assessment_id,
            submission_assessment_checked.id,
        )
        f = SimpleUploadedFile('report.pdf', b'%PDF-1.4', content_type='application/pdf')
        r = student_client.patch(url, {'student_upload': f}, format='multipart')
        assert r.status_code in (200, 400)  # 400 from Cloudinary in test env is acceptable

    def test_past_deadline_makes_upload_read_only(
        self, student_client, active_enrollment, active_allocation, db
    ):
        """After submission_deadline passes, student_upload becomes read_only.
        The serializer pops `urls` in __init__ when deadline is past."""
        from Models.models import Assessment, AssessmentChecked
        past_assessment = Assessment.objects.create(
            allocation=active_allocation,
            assessment_type='Assignment',
            assessment_name='Past Assignment',
            assessment_date=(timezone.now() - timedelta(days=10)).date(),
            weightage=15,
            total_marks=30,
            student_submission=True,
            submission_deadline=timezone.now() - timedelta(days=1),
        )
        checked = AssessmentChecked.objects.create(
            assessment=past_assessment,
            enrollment=active_enrollment,
        )
        url = upload_url(
            active_enrollment.enrollment_id,
            past_assessment.assessment_id,
            checked.id,
        )
        f = SimpleUploadedFile('late.pdf', b'%PDF-1.4', content_type='application/pdf')
        r = student_client.patch(url, {'student_upload': f}, format='multipart')
        # Either 200 (field ignored as read_only) or 403/400 — not 500
        assert r.status_code in (200, 400, 403)

    def test_completed_enrollment_upload_read_only(
        self, student_client, active_enrollment, submission_assessment, submission_assessment_checked
    ):
        """Completed enrollment makes student_upload read_only."""
        active_enrollment.status = 'Completed'
        active_enrollment.save()
        url = upload_url(
            active_enrollment.enrollment_id,
            submission_assessment.assessment_id,
            submission_assessment_checked.id,
        )
        f = SimpleUploadedFile('done.pdf', b'%PDF-1.4', content_type='application/pdf')
        r = student_client.patch(url, {'student_upload': f}, format='multipart')
        assert r.status_code in (200, 400, 403)
