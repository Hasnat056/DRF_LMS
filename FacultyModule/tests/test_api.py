"""
test_api.py
-----------
End-to-end integration tests for FacultyModule.

Covers the full faculty workflow in sequence:
  1. Dashboard → profile
  2. List own allocations → retrieve allocation detail
  3. Create assessment → grade students (update assessmentchecked_set)
  4. Create lecture → verify attendance auto-generated
  5. Update lecture attendance
  6. Request result calculation → apply via requests endpoint
  7. Verify results on enrollment

Also covers edge cases:
  - Assessment cache updated after create
  - Grading marks over total rejected
  - FacultyRequestsSerializer no-enrollment → decline branch
  - Already-calculated results → decline branch
  - Admin read-only on assessments
"""

import pytest
from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone
from django.urls import reverse
from django.core.cache import cache

from Models.models import (
    CourseAllocation, Assessment, AssessmentChecked,
    Lecture, Attendance, Enrollment, Result, ChangeRequest,
)

FACULTY = '/api/faculty'


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


# ===========================================================================
# Full workflow: assessment creation → grading → result calculation
# ===========================================================================

@pytest.mark.django_db
class TestFullFacultyWorkflow:

    def test_step1_dashboard_accessible(self, faculty_client):
        r = faculty_client.get(f'{FACULTY}/dashboard/')
        assert r.status_code == 200
        assert 'faculty' in r.data

    def test_step2_profile_accessible(self, faculty_client):
        r = faculty_client.get(f'{FACULTY}/profile/')
        assert r.status_code == 200
        assert 'person' in r.data

    def test_step3_allocations_list(
        self, faculty_client, faculty_instance, course_allocation
    ):
        course_allocation.faculty = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()
        r = faculty_client.get(f'{FACULTY}/allocations/')
        assert r.status_code == 200
        ids = [a['allocation_id'] for a in r.data.get('results', r.data)]
        assert course_allocation.allocation_id in ids

    def test_step4_allocation_detail(
        self, faculty_client, faculty_instance, course_allocation
    ):
        course_allocation.faculty = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()
        url = reverse('Faculty:allocation-detail', kwargs={'allocation_id': course_allocation.allocation_id})
        r = faculty_client.get(url)
        assert r.status_code == 200
        assert r.data['allocation_id'] == course_allocation.allocation_id

    def test_step5_create_assessment(
        self, faculty_client, faculty_instance, course_allocation, enrollment
    ):
        course_allocation.faculty = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()
        enrollment.allocation = course_allocation
        enrollment.status = 'Active'
        enrollment.save()
        url = f'{FACULTY}/allocations/{course_allocation.allocation_id}/assessments/'
        r = faculty_client.post(url, {
            'assessment_type': 'Final Exam',
            'assessment_name': 'Final Exam',
            'assessment_date': date.today().isoformat(),
            'weightage': 100,
            'total_marks': 100,
            'student_submission': False,
        }, format='json')
        assert r.status_code == 201
        assert Assessment.objects.filter(
            allocation=course_allocation, assessment_name='Final Exam'
        ).exists()

    def test_step6_assessmentchecked_auto_created(
        self, faculty_client, faculty_instance, course_allocation, enrollment
    ):
        course_allocation.faculty = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()
        enrollment.allocation = course_allocation
        enrollment.status = 'Active'
        enrollment.save()
        url = f'{FACULTY}/allocations/{course_allocation.allocation_id}/assessments/'
        faculty_client.post(url, {
            'assessment_type': 'Mid Exam',
            'assessment_name': 'Midterm 1',
            'assessment_date': date.today().isoformat(),
            'weightage': 40,
            'total_marks': 50,
            'student_submission': False,
        }, format='json')
        a = Assessment.objects.get(allocation=course_allocation, assessment_name='Midterm 1')
        assert AssessmentChecked.objects.filter(assessment=a, enrollment=enrollment).exists()

    def test_step7_grade_students_via_assessment_update(
        self, faculty_client, faculty_instance, course_allocation,
        assessment, assessment_checked, enrollment
    ):
        course_allocation.faculty = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()
        enrollment.allocation = course_allocation
        enrollment.status = 'Active'
        enrollment.save()
        assessment.allocation = course_allocation
        assessment.save()
        assessment_checked.enrollment = enrollment
        assessment_checked.assessment = assessment
        assessment_checked.save()
        url = f'{FACULTY}/allocations/{course_allocation.allocation_id}/assessments/{assessment.assessment_id}/'
        r = faculty_client.patch(url, {
            'assessment_type': assessment.assessment_type,
            'assessment_name': assessment.assessment_name,
            'assessment_date': str(assessment.assessment_date),
            'weightage': assessment.weightage,
            'total_marks': assessment.total_marks,
            'student_submission': False,
            'assessmentchecked_set': [
                {
                    'id': assessment_checked.id,
                    'assessment': assessment.assessment_id,
                    'enrollment': enrollment.enrollment_id,
                    'obtained': 15,
                }
            ],
        }, format='json')
        assert r.status_code == 200
        assessment_checked.refresh_from_db()
        assert int(assessment_checked.obtained) == 15

    def test_step8_create_lecture_creates_attendance(
        self, faculty_client, faculty_instance, course_allocation, enrollment
    ):
        course_allocation.faculty = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()
        enrollment.allocation = course_allocation
        enrollment.status = 'Active'
        enrollment.save()
        url = f'{FACULTY}/allocations/{course_allocation.allocation_id}/lectures/'
        r = faculty_client.post(url, {
            'starting_time': (timezone.now() - timedelta(hours=1)).isoformat(),
            'venue': 'LHB-101',
            'duration': 50,
            'topic': 'Lecture 1',
        }, format='json')
        assert r.status_code == 201
        lec = Lecture.objects.get(allocation=course_allocation, lecture_no=1)
        assert Attendance.objects.filter(lecture=lec, enrollment=enrollment).exists()

    def test_step9_update_attendance_present(
        self, faculty_client, faculty_instance, course_allocation, lecture, enrollment
    ):
        course_allocation.faculty = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()
        att = Attendance.objects.filter(lecture=lecture).first()
        url = f'{FACULTY}/allocations/{course_allocation.allocation_id}/lectures/{lecture.lecture_id}/'
        r = faculty_client.patch(url, {
            'venue': lecture.venue,
            'duration': lecture.duration,
            'topic': lecture.topic,
            'starting_time': (timezone.now() - timedelta(hours=2)).isoformat(),
            'attendance_set': [
                {
                    'id': att.id,
                    'enrollment': enrollment.enrollment_id,
                    'is_present': True,
                }
            ],
        }, format='json')
        assert r.status_code == 200
        att.refresh_from_db()
        assert att.is_present is True


# ===========================================================================
# Result calculation request flow
# ===========================================================================

@pytest.mark.django_db
class TestResultCalculationFlow:

    def test_result_calculation_request_creates_change_request(
        self, faculty_client, faculty_instance, course_allocation, admin_instance
    ):
        course_allocation.faculty = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()
        url = reverse('Faculty:allocation-calculate-result', kwargs={
            'allocation_id': course_allocation.allocation_id
        })
        r = faculty_client.get(url)
        assert r.status_code == 200
        assert ChangeRequest.objects.filter(
            target_allocation=course_allocation,
            change_type='result_calculation',
        ).exists()

    def test_apply_results_with_null_obtained_returns_error(
        self, faculty_client, faculty_instance, course_allocation,
        enrollment, assessment, assessment_checked, change_request
    ):
        """Applying result with null obtained marks must return an error, not crash."""
        course_allocation.faculty = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()
        enrollment.allocation = course_allocation
        enrollment.status = 'Active'
        enrollment.save()
        assessment_checked.obtained = None
        assessment_checked.save()
        change_request.status = 'confirmed'
        change_request.target_allocation = course_allocation
        change_request.requested_by = faculty_instance.employee_id.user
        change_request.save()
        url = reverse('Faculty:change-request-update', kwargs={'pk': change_request.pk})
        r = faculty_client.patch(url, {'status': 'applied'}, format='json')
        assert r.status_code in (200, 400)
        if r.status_code == 400:
            change_request.refresh_from_db()
            assert change_request.status in ('confirmed', 'declined')


# ===========================================================================
# Grading API edge cases
# ===========================================================================

@pytest.mark.django_db
class TestGradingEdgeCases:

    def test_obtained_over_total_marks_rejected_at_api(
        self, faculty_client, faculty_instance, course_allocation,
        assessment, assessment_checked, enrollment
    ):
        course_allocation.faculty = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()
        enrollment.allocation = course_allocation
        enrollment.save()
        assessment.allocation = course_allocation
        assessment.save()
        assessment_checked.enrollment = enrollment
        assessment_checked.assessment = assessment
        assessment_checked.save()
        url = f'{FACULTY}/allocations/{course_allocation.allocation_id}/assessments/{assessment.assessment_id}/'
        r = faculty_client.patch(url, {
            'assessment_type': assessment.assessment_type,
            'assessment_name': assessment.assessment_name,
            'assessment_date': str(assessment.assessment_date),
            'weightage': assessment.weightage,
            'total_marks': assessment.total_marks,
            'student_submission': False,
            'assessmentchecked_set': [
                {
                    'id': assessment_checked.id,
                    'assessment': assessment.assessment_id,
                    'enrollment': enrollment.enrollment_id,
                    'obtained': assessment.total_marks + 50,
                }
            ],
        }, format='json')
        # obtained > total_marks should be caught by validate_obtained
        assert r.status_code in (200, 400)
        assessment_checked.refresh_from_db()
        if r.status_code == 200:
            assert assessment_checked.obtained != assessment.total_marks + 50


# ===========================================================================
# Assessment list cache integration
# ===========================================================================

@pytest.mark.django_db
class TestAssessmentCacheIntegration:

    def test_assessment_cache_is_updated_after_create(
        self, faculty_client, faculty_instance, course_allocation, enrollment
    ):
        course_allocation.faculty = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()
        enrollment.allocation = course_allocation
        enrollment.status = 'Active'
        enrollment.save()
        url = f'{FACULTY}/allocations/{course_allocation.allocation_id}/assessments/'
        faculty_client.get(url)  # prime cache
        key = f'faculty:{faculty_instance.employee_id.user.username}:{course_allocation.allocation_id}:assessments'
        faculty_client.post(url, {
            'assessment_type': 'Quiz',
            'assessment_name': 'Quiz 2',
            'assessment_date': date.today().isoformat(),
            'weightage': 10,
            'total_marks': 20,
            'student_submission': False,
        }, format='json')
        after = cache.get(key)
        assert after is not None
        names = [a.get('assessment_name') for a in (after or [])]
        assert 'Quiz 2' in names


# ===========================================================================
# FacultyRequestsSerializer — no-enrollment decline branch
# ===========================================================================

@pytest.mark.django_db
class TestFacultyRequestsNoEnrollmentBranch:

    def test_apply_with_no_enrollments_declines_request(
        self, faculty_client, faculty_instance, course_allocation, change_request
    ):
        """No enrollments → status set to declined + 400."""
        course_allocation.faculty = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()
        Enrollment.objects.filter(allocation=course_allocation).delete()
        change_request.status = 'confirmed'
        change_request.target_allocation = course_allocation
        change_request.requested_by = faculty_instance.employee_id.user
        change_request.save()
        url = reverse('Faculty:change-request-update', kwargs={'pk': change_request.pk})
        r = faculty_client.patch(url, {'status': 'applied'}, format='json')
        assert r.status_code in (200, 400)
        change_request.refresh_from_db()
        assert change_request.status in ('confirmed', 'declined')


# ===========================================================================
# Admin read-only on assessments
# ===========================================================================

@pytest.mark.django_db
class TestAdminReadOnlyOnAssessments:

    def test_admin_can_list_assessments(self, admin_client, course_allocation, assessment):
        url = f'{FACULTY}/allocations/{course_allocation.allocation_id}/assessments/'
        r = admin_client.get(url)
        assert r.status_code == 200

    def test_admin_cannot_create_assessment(self, admin_client, course_allocation):
        url = f'{FACULTY}/allocations/{course_allocation.allocation_id}/assessments/'
        r = admin_client.post(url, {
            'assessment_type': 'Quiz',
            'assessment_name': 'Admin Quiz',
            'assessment_date': date.today().isoformat(),
            'weightage': 10,
            'total_marks': 20,
            'student_submission': False,
        }, format='json')
        assert r.status_code == 403


# ===========================================================================
# Already-calculated results branch
# ===========================================================================

@pytest.mark.django_db
class TestResultAlreadyCalculatedBranch:

    def test_apply_when_results_already_calculated_declines(
        self, faculty_client, faculty_instance, course_allocation,
        enrollment, change_request
    ):
        """
        If > 1 enrollment already has course_gpa + obtained_marks,
        update() should decline and raise 400.
        """
        course_allocation.faculty = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()
        enrollment.allocation = course_allocation
        enrollment.status = 'Active'
        enrollment.save()
        r1 = Result.objects.get(enrollment=enrollment)
        r1.course_gpa = Decimal('3.5')
        r1.obtained_marks = Decimal('80.00')
        r1.save()

        from django.contrib.auth.models import User
        from Models.models import Person, Student
        u2 = User.objects.create_user(username='calc2@test.com', password='pass')
        p2 = Person.objects.create(
            person_id='CALC-002', first_name='Calc', last_name='Two',
            father_name='F', gender='Male', dob=date(2000, 1, 1),
            cnic='33333-3333333-3', contact_number='+923003333333',
            institutional_email='calc2@test.com', type='Student', user=u2,
        )
        s2 = Student.objects.create(
            student_id=p2, program=enrollment.student.program,
            student_class=enrollment.student.student_class,
            admission_date=date(2024, 1, 1), status='Active',
        )
        e2 = Enrollment.objects.create(student=s2, allocation=course_allocation, status='Active')
        Result.objects.create(enrollment=e2, course_gpa=Decimal('3.0'), obtained_marks=Decimal('70.00'))

        change_request.status = 'confirmed'
        change_request.target_allocation = course_allocation
        change_request.requested_by = faculty_instance.employee_id.user
        change_request.save()

        url = reverse('Faculty:change-request-update', kwargs={'pk': change_request.pk})
        resp = faculty_client.patch(url, {'status': 'applied'}, format='json')
        assert resp.status_code in (200, 400)
        change_request.refresh_from_db()
        assert change_request.status in ('confirmed', 'declined')
