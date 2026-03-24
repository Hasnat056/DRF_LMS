"""
test_faculty_views.py
---------------------
HTTP integration tests for FacultyModule views.

Covers:
  - FacultyDashboardView      : auth, cache, division by zero bug
  - FacultyProfileView        : auth, cache read/write, update invalidates cache
  - FacultyCourseAllocationView : only Ongoing/Completed shown, cache behavior
  - AssessmentListCreateAPIView : CRUD, permission guards, cache population
  - LectureListCreateAPIView    : CRUD, auto attendance creation
  - ResultCalculationRequest    : permission guards, duplicate request guard
  - FacultyRequestsListView     : only own requests shown
"""

import pytest
from datetime import timedelta, date
from django.utils import timezone
from django.core.cache import cache
from django.urls import reverse

from Models.models import (
    CourseAllocation, Assessment, AssessmentChecked,
    Lecture, Attendance, Enrollment, ChangeRequest, Result,
)

FACULTY = '/api/faculty'


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


# ===========================================================================
# FacultyDashboardView
# ===========================================================================

@pytest.mark.django_db
class TestFacultyDashboardView:

    def test_requires_authentication(self, anon_client):
        response = anon_client.get(f'{FACULTY}/dashboard/')
        assert response.status_code == 401

    def test_admin_cannot_access_faculty_dashboard(self, admin_client):
        response = admin_client.get(f'{FACULTY}/dashboard/')
        assert response.status_code == 403

    def test_faculty_can_access_dashboard(self, faculty_client):
        response = faculty_client.get(f'{FACULTY}/dashboard/')
        assert response.status_code == 200

    def test_dashboard_returns_expected_fields(self, faculty_client):
        response = faculty_client.get(f'{FACULTY}/dashboard/')
        assert response.status_code == 200
        for field in ['faculty', 'course_allocation_count', 'active_allocations',
                      'completed_allocations', 'allocation_average_success']:
            assert field in response.data, f"Missing field: {field}"

    def test_dashboard_is_cached_on_first_request(self, faculty_client, faculty_instance):
        key = f'faculty:dashboard:{faculty_instance.employee_id.user.username}'
        assert cache.get(key) is None
        faculty_client.get(f'{FACULTY}/dashboard/')
        assert cache.get(key) is not None

    def test_dashboard_served_from_cache_on_second_request(
        self, faculty_client, faculty_instance
    ):
        faculty_client.get(f'{FACULTY}/dashboard/')
        key = f'faculty:dashboard:{faculty_instance.employee_id.user.username}'
        assert cache.get(key) is not None
        response = faculty_client.get(f'{FACULTY}/dashboard/')
        assert response.status_code == 200

    def test_bug_division_by_zero_when_completed_allocation_has_no_enrollments(
        self, faculty_client, faculty_instance, course_allocation, db
    ):
        """
        BUG: FacultyDashboardView computes:
            average = sum(...) / each.enrollment_set.all().count()
        If a Completed allocation has zero enrollments → ZeroDivisionError → 500.
        """
        course_allocation.teacher_id = faculty_instance
        course_allocation.status = 'Completed'
        course_allocation.save()
        # no enrollments created — enrollment_set.count() == 0

        response = faculty_client.get(f'{FACULTY}/dashboard/')
        # should be 200, not 500
        assert response.status_code == 200, (
            f"BUG: Got {response.status_code} — "
            "division by zero when completed allocation has no enrollments"
        )


# ===========================================================================
# FacultyProfileView
# ===========================================================================

@pytest.mark.django_db
class TestFacultyProfileView:

    def test_requires_authentication(self, anon_client):
        response = anon_client.get(f'{FACULTY}/profile/')
        assert response.status_code == 401

    def test_student_cannot_access_faculty_profile(self, student_client):
        response = student_client.get(f'{FACULTY}/profile/')
        assert response.status_code == 403

    def test_faculty_can_view_own_profile(self, faculty_client):
        response = faculty_client.get(f'{FACULTY}/profile/')
        assert response.status_code == 200

    def test_profile_cache_populated_on_first_request(
        self, faculty_client, faculty_instance
    ):
        key = f'faculty:{faculty_instance.employee_id.user.username}'
        assert cache.get(key) is None
        faculty_client.get(f'{FACULTY}/profile/')
        assert cache.get(key) is not None

    def test_faculty_cannot_post_to_profile(self, faculty_client):
        """FacultyPermissions blocks POST."""
        response = faculty_client.post(f'{FACULTY}/profile/', {}, format='json')
        assert response.status_code in (403, 405)


# ===========================================================================
# FacultyCourseAllocationView
# ===========================================================================

@pytest.mark.django_db
class TestFacultyCourseAllocationView:

    def test_requires_authentication(self, anon_client):
        response = anon_client.get(f'{FACULTY}/allocations/')
        assert response.status_code == 401

    def test_only_own_allocations_returned(
        self, faculty_client, faculty_instance, course_allocation, db
    ):
        """Faculty must only see their own allocations."""
        course_allocation.teacher_id = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()

        response = faculty_client.get(f'{FACULTY}/allocations/')
        assert response.status_code == 200
        for alloc in response.data.get('results', response.data):
            assert alloc['teacher_id'] == faculty_instance.employee_id.person_id

    def test_inactive_allocations_not_shown(
        self, faculty_client, faculty_instance, course_allocation
    ):
        """Inactive allocations must not appear in faculty's allocation list."""
        course_allocation.teacher_id = faculty_instance
        course_allocation.status = 'Inactive'
        course_allocation.save()

        response = faculty_client.get(f'{FACULTY}/allocations/')
        assert response.status_code == 200
        ids = [a.get('allocation_id') for a in response.data.get('results', response.data)]
        assert course_allocation.allocation_id not in ids

    def test_ongoing_and_completed_allocations_shown(
        self, faculty_client, faculty_instance, course_allocation, db
    ):
        """Only Ongoing and Completed allocations must be returned."""
        for status_val in ['Ongoing', 'Completed']:
            course_allocation.teacher_id = faculty_instance
            course_allocation.status = status_val
            course_allocation.save()
            response = faculty_client.get(f'{FACULTY}/allocations/')
            assert response.status_code == 200

    def test_allocation_detail_accessible(
        self, faculty_client, faculty_instance, course_allocation
    ):
        course_allocation.teacher_id = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()

        url = reverse('Faculty:allocation-detail', kwargs={
            'allocation_id': course_allocation.allocation_id
        })
        response = faculty_client.get(url)
        assert response.status_code == 200

    def test_another_faculty_cannot_access_allocation(
        self, faculty_client, course_allocation, db
    ):
        """Faculty must not be able to access another faculty's allocation."""
        from django.contrib.auth.models import User, Group
        from Models.models import Person, Faculty, Department
        other_user = User.objects.create_user(
            username='other@faculty.com', password='pass123'
        )
        other_user.groups.add(Group.objects.get(name='Faculty'))
        dept = Department.objects.first()
        other_person = Person.objects.create(
            person_id='OTHER-001', first_name='Other', last_name='Faculty',
            type='Faculty', user=other_user,
            institutional_email='other@faculty.com',
            dob='1980-01-01',
        )
        other_faculty = Faculty.objects.create(
            employee_id=other_person,
            department_id=dept,
            designation='Lecturer',
        )
        course_allocation.teacher_id = other_faculty
        course_allocation.save()

        url = reverse('Faculty:allocation-detail', kwargs={
            'allocation_id': course_allocation.allocation_id
        })
        response = faculty_client.get(url)
        assert response.status_code == 403


# ===========================================================================
# AssessmentListCreateAPIView
# ===========================================================================

@pytest.mark.django_db
class TestAssessmentAPI:

    def test_requires_authentication(self, anon_client, course_allocation):
        url = f'{FACULTY}/allocations/{course_allocation.allocation_id}/assessments/'
        response = anon_client.get(url)
        assert response.status_code == 401

    def test_faculty_can_list_assessments(self, faculty_client, faculty_instance, course_allocation):
        course_allocation.teacher_id = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()

        url = f'{FACULTY}/allocations/{course_allocation.allocation_id}/assessments/'
        response = faculty_client.get(url)
        assert response.status_code == 200

    def test_faculty_can_create_assessment(
            self, faculty_client, faculty_instance, course_allocation, enrollment
    ):
        course_allocation.teacher_id = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()
        enrollment.allocation_id = course_allocation
        enrollment.status = 'Active'
        enrollment.save()

        url = f'{FACULTY}/allocations/{course_allocation.allocation_id}/assessments/'
        response = faculty_client.post(url, {
            'assessment_type': 'Quiz',
            'assessment_name': 'Quiz 1',
            'assessment_date': date.today().isoformat(),
            'weightage': 10,
            'total_marks': 20,
            'student_submission': False,
        }, format='json')
        assert response.status_code == 201

    def test_create_assessment_auto_creates_assessment_checked(
        self, faculty_client, faculty_instance, course_allocation, enrollment
    ):
        """Creating an assessment must auto-create AssessmentChecked for all enrollments."""
        course_allocation.teacher_id = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()
        enrollment.allocation_id = course_allocation
        enrollment.save()

        url = f'{FACULTY}/allocations/{course_allocation.allocation_id}/assessments/'
        response = faculty_client.post(url, {
            'assessment_type': 'Quiz',
            'assessment_name': 'Quiz 1',
            'assessment_date': date.today().isoformat(),
            'weightage': 10,
            'total_marks': 20,
            'student_submission': False,
        }, format='json')
        assert response.status_code == 201
        assessment = Assessment.objects.get(
            allocation_id=course_allocation, assessment_name='Quiz 1'
        )
        assert AssessmentChecked.objects.filter(assessment_id=assessment).count() == 1

    def test_admin_can_only_read_assessments(self, admin_client, course_allocation):
        """Admin has read-only access to assessments."""
        url = f'{FACULTY}/allocations/{course_allocation.allocation_id}/assessments/'
        response = admin_client.get(url)
        assert response.status_code == 200

        response = admin_client.post(url, {
            'assessment_type': 'Quiz',
            'assessment_name': 'Quiz 1',
            'assessment_date': date.today().isoformat(),
            'weightage': 10,
            'total_marks': 20,
            'student_submission': False,
        }, format='json')
        assert response.status_code == 403

    def test_assessment_cache_populated_on_list(
        self, faculty_client, faculty_instance, course_allocation
    ):
        course_allocation.teacher_id = faculty_instance
        course_allocation.save()
        key = f'faculty:{faculty_instance.employee_id.user.username}:{course_allocation.allocation_id}:assessments'
        assert cache.get(key) is None
        url = f'{FACULTY}/allocations/{course_allocation.allocation_id}/assessments/'
        faculty_client.get(url)
        assert cache.get(key) is not None


# ===========================================================================
# LectureListCreateAPIView
# ===========================================================================

@pytest.mark.django_db
class TestLectureAPI:

    def test_requires_authentication(self, anon_client, course_allocation):
        url = f'{FACULTY}/allocations/{course_allocation.allocation_id}/lectures/'
        response = anon_client.get(url)
        assert response.status_code == 401

    def test_faculty_can_list_lectures(
        self, faculty_client, faculty_instance, course_allocation
    ):
        course_allocation.teacher_id = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()

        url = f'{FACULTY}/allocations/{course_allocation.allocation_id}/lectures/'
        response = faculty_client.get(url)
        assert response.status_code == 200

    def test_create_lecture_auto_creates_attendance(
        self, faculty_client, faculty_instance, course_allocation, enrollment
    ):
        """Creating a lecture must auto-create Attendance for all enrolled students."""
        course_allocation.teacher_id = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()
        enrollment.allocation_id = course_allocation
        enrollment.save()

        url = f'{FACULTY}/allocations/{course_allocation.allocation_id}/lectures/'
        response = faculty_client.post(url, {
            'starting_time': (timezone.now() - timedelta(hours=1)).isoformat(),
            'venue': 'Room 101',
            'duration': 60,
            'topic': 'Intro',
        }, format='json')
        assert response.status_code == 201
        lecture = Lecture.objects.get(allocation_id=course_allocation)
        assert Attendance.objects.filter(lecture_id=lecture).count() == 1

    def test_student_cannot_create_lecture(self, student_client, course_allocation):
        url = f'{FACULTY}/allocations/{course_allocation.allocation_id}/lectures/'
        response = student_client.post(url, {
            'starting_time': (timezone.now() - timedelta(hours=1)).isoformat(),
            'venue': 'Room 101',
            'duration': 60,
            'topic': 'Unauthorized',
        }, format='json')
        assert response.status_code == 403


# ===========================================================================
# ResultCalculationRequest
# ===========================================================================

@pytest.mark.django_db
class TestResultCalculationRequest:

    def test_requires_authentication(self, anon_client, course_allocation):
        url = reverse('Faculty:allocation-calculate-result', kwargs={
            'allocation_id': course_allocation.allocation_id
        })
        response = anon_client.get(url)
        assert response.status_code == 401

    def test_faculty_can_request_result_calculation(
        self, faculty_client, faculty_instance, course_allocation, admin_instance,
            db
    ):
        course_allocation.teacher_id = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()

        url = reverse('Faculty:allocation-calculate-result', kwargs={
            'allocation_id': course_allocation.allocation_id
        })
        response = faculty_client.get(url)
        assert response.status_code == 200

    def test_duplicate_pending_request_blocked(
        self, faculty_client, faculty_instance, course_allocation, db
    ):
        """If a pending request already exists, a new one must be blocked."""
        course_allocation.teacher_id = faculty_instance
        course_allocation.save()
        ChangeRequest.objects.create(
            change_type='result_calculation',
            target_allocation=course_allocation,
            requested_by=faculty_instance.employee_id.user,
            status='pending',
        )
        url = reverse('Faculty:allocation-calculate-result', kwargs={
            'allocation_id': course_allocation.allocation_id
        })
        response = faculty_client.get(url)
        assert response.status_code == 200
        assert 'pending' in response.data.get('message', '').lower()

    def test_another_faculty_cannot_request_for_others_allocation(
            self, faculty_client, course_allocation, admin_instance, db
    ):
        """Faculty must not be able to request result calculation for another's allocation."""
        # course_allocation teacher_id is NOT set to the logged-in faculty
        # so ownership check should return 403
        from django.contrib.auth.models import User, Group
        from Models.models import Person, Faculty
        other_user = User.objects.create_user(
            username='other@faculty.com', password='pass123'
        )
        other_person = Person.objects.create(
            person_id='OTHER-FAC-001', first_name='Other', last_name='Faculty',
            father_name='Father', gender='Male', dob=date(1980, 1, 1),
            cnic='12345-1234567-9', contact_number='+923001234560',
            institutional_email='other@faculty.com', type='Faculty', user=other_user,
        )
        other_faculty = Faculty.objects.create(
            employee_id=other_person,
            department_id=course_allocation.teacher_id.department_id,
            designation='Lecturer',
            joining_date=date(2021, 1, 1),
        )
        course_allocation.teacher_id = other_faculty
        course_allocation.save()

        url = reverse('Faculty:allocation-calculate-result', kwargs={
            'allocation_id': course_allocation.allocation_id
        })
        response = faculty_client.get(url)
        assert response.status_code == 403



# ===========================================================================
# FacultyRequestsListView
# ===========================================================================

@pytest.mark.django_db
class TestFacultyRequestsListView:

    def test_requires_authentication(self, anon_client):
        response = anon_client.get(f'{FACULTY}/requests/')
        assert response.status_code == 401

    def test_faculty_only_sees_own_requests(
        self, faculty_client, faculty_instance, course_allocation, db
    ):
        """Faculty must only see their own change requests."""
        ChangeRequest.objects.create(
            change_type='result_calculation',
            target_allocation=course_allocation,
            requested_by=faculty_instance.employee_id.user,
            status='pending',
        )
        response = faculty_client.get(f'{FACULTY}/requests/')
        assert response.status_code == 200
        for req in response.data.get('results', response.data):
            assert req['requested_by'] == faculty_instance.employee_id.user.pk

    def test_admin_cannot_access_faculty_requests(self, admin_client):
        response = admin_client.get(f'{FACULTY}/requests/')
        assert response.status_code == 403
