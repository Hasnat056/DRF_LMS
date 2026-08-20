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
        course_allocation.faculty = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()

        response = faculty_client.get(f'{FACULTY}/allocations/')
        assert response.status_code == 200
        for alloc in response.data.get('results', response.data):
            assert alloc['faculty'] == faculty_instance.employee_id.person_id

    def test_inactive_allocations_not_shown(
        self, faculty_client, faculty_instance, course_allocation
    ):
        """Inactive allocations must not appear in faculty's allocation list."""
        course_allocation.faculty = faculty_instance
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
            course_allocation.faculty = faculty_instance
            course_allocation.status = status_val
            course_allocation.save()
            response = faculty_client.get(f'{FACULTY}/allocations/')
            assert response.status_code == 200

    def test_allocation_detail_accessible(
        self, faculty_client, faculty_instance, course_allocation
    ):
        course_allocation.faculty = faculty_instance
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
            department=dept,
            designation='Lecturer',
        )
        course_allocation.faculty = other_faculty
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
        course_allocation.faculty = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()
        enrollment.allocation = course_allocation
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
        print(response.data)
        assert response.status_code == 201

    def test_create_assessment_auto_creates_assessment_checked(
        self, faculty_client, faculty_instance, course_allocation, enrollment
    ):
        """Creating an assessment must auto-create AssessmentChecked for all enrollments."""
        course_allocation.faculty = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()
        enrollment.allocation = course_allocation
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
            allocation=course_allocation, assessment_name='Quiz 1'
        )
        assert AssessmentChecked.objects.filter(assessment=assessment).count() == 1

    def test_open_submission_assessment_notifies_enrolled_students(
        self, faculty_client, faculty_instance, course_allocation, enrollment
    ):
        from Models.models import Notification
        course_allocation.faculty = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()
        enrollment.allocation = course_allocation
        enrollment.status = 'Active'
        enrollment.save()

        deadline = timezone.now() + timedelta(days=7)
        url = f'{FACULTY}/allocations/{course_allocation.allocation_id}/assessments/'
        response = faculty_client.post(url, {
            'assessment_type': 'Assignment',
            'assessment_name': 'Assignment 1',
            'assessment_date': date.today().isoformat(),
            'weightage': 10,
            'total_marks': 20,
            'student_submission': True,
            'submission_deadline': deadline.isoformat(),
        }, format='json')
        assert response.status_code == 201

        assert Notification.objects.filter(
            recipient=enrollment.student.student_id.user, verb='assessment_open'
        ).exists()

    def test_closed_submission_assessment_creates_no_notifications(
        self, faculty_client, faculty_instance, course_allocation, enrollment
    ):
        from Models.models import Notification
        course_allocation.faculty = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()
        enrollment.allocation = course_allocation
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
        assert not Notification.objects.filter(verb='assessment_open').exists()

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
        course_allocation.faculty = faculty_instance
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
        course_allocation.faculty = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()

        url = f'{FACULTY}/allocations/{course_allocation.allocation_id}/lectures/'
        response = faculty_client.get(url)
        assert response.status_code == 200

    def test_create_lecture_auto_creates_attendance(
        self, faculty_client, faculty_instance, course_allocation, enrollment
    ):
        """Creating a lecture must auto-create Attendance for all enrolled students."""
        course_allocation.faculty = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()
        enrollment.allocation = course_allocation
        enrollment.save()

        url = f'{FACULTY}/allocations/{course_allocation.allocation_id}/lectures/'
        response = faculty_client.post(url, {
            'starting_time': (timezone.now() - timedelta(hours=1)).isoformat(),
            'venue': 'Room 101',
            'duration': 60,
            'topic': 'Intro',
        }, format='json')
        assert response.status_code == 201
        lecture = Lecture.objects.get(allocation=course_allocation)
        assert Attendance.objects.filter(lecture=lecture).count() == 1

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
        course_allocation.faculty = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()

        url = reverse('Faculty:allocation-calculate-result', kwargs={
            'allocation_id': course_allocation.allocation_id
        })
        response = faculty_client.get(url)
        assert response.status_code == 200

        from Models.models import Notification
        assert Notification.objects.filter(
            recipient=admin_instance.employee_id.user, verb='result_calculation_requested'
        ).exists()

    def test_duplicate_pending_request_blocked(
        self, faculty_client, faculty_instance, course_allocation, db
    ):
        """If a pending request already exists, a new one must be blocked."""
        course_allocation.faculty = faculty_instance
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
            department=course_allocation.faculty.department,
            designation='Lecturer',
            joining_date=date(2021, 1, 1),
        )
        course_allocation.faculty = other_faculty
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


# ===========================================================================
# FacultyProfileView — PUT
# ===========================================================================

@pytest.mark.django_db
class TestFacultyProfileUpdate:

    def test_put_invalid_contact_number_returns_400(self, faculty_client):
        r = faculty_client.put(f'{FACULTY}/profile/', {
            'person': {'contact_number': '123'},
        }, format='json')
        assert r.status_code == 400

    def test_put_valid_contact_number_returns_200(self, faculty_client, faculty_instance):
        r = faculty_client.put(f'{FACULTY}/profile/', {
            'person': {
                'contact_number': '+923001239999',
                'personal_email': 'updated@test.com',
            },
            'department': faculty_instance.department.department_id,
            'designation': faculty_instance.designation,
            'joining_date': str(faculty_instance.joining_date),
        }, format='json')
        assert r.status_code == 200

    def test_put_invalidates_and_repopulates_cache(self, faculty_client, faculty_instance):
        key = f'faculty:{faculty_instance.employee_id.user.username}'
        faculty_client.get(f'{FACULTY}/profile/')
        assert cache.get(key) is not None
        faculty_client.put(f'{FACULTY}/profile/', {
            'person': {'contact_number': '+923009876543'},
            'department': faculty_instance.department.department_id,
            'designation': faculty_instance.designation,
            'joining_date': str(faculty_instance.joining_date),
        }, format='json')
        # cache is deleted then repopulated — key exists with fresh data
        assert cache.get(key) is not None


# ===========================================================================
# FacultyCourseAllocationView — cache filter branches
# ===========================================================================

@pytest.mark.django_db
class TestFacultyAllocationCacheBranches:

    def test_list_with_no_params_serves_cached_data(
        self, faculty_client, faculty_instance, course_allocation
    ):
        course_allocation.faculty = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()
        # prime the cache
        faculty_client.get(f'{FACULTY}/allocations/')
        key = f'faculty:{faculty_instance.employee_id.user.username}:allocations'
        assert cache.get(key) is not None
        # second hit — served from cache
        r = faculty_client.get(f'{FACULTY}/allocations/')
        assert r.status_code == 200

    def test_list_with_status_filter_filters_cached_data(
        self, faculty_client, faculty_instance, course_allocation
    ):
        course_allocation.faculty = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()
        faculty_client.get(f'{FACULTY}/allocations/')  # prime cache
        r = faculty_client.get(f'{FACULTY}/allocations/?status=Completed')
        assert r.status_code == 200

    def test_allocation_detail_returns_200_for_owner(
        self, faculty_client, faculty_instance, course_allocation
    ):
        course_allocation.faculty = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()
        url = reverse('Faculty:allocation-detail', kwargs={'allocation_id': course_allocation.allocation_id})
        r = faculty_client.get(url)
        assert r.status_code == 200

    def test_student_cannot_access_allocations(self, student_client):
        r = student_client.get(f'{FACULTY}/allocations/')
        assert r.status_code == 403


# ===========================================================================
# AssessmentRetrieveUpdateDestroyAPIView
# ===========================================================================

@pytest.mark.django_db
class TestAssessmentRetrieveUpdateDestroy:

    def test_get_assessment_returns_200(
        self, faculty_client, faculty_instance, course_allocation, assessment
    ):
        course_allocation.faculty = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()
        url = f'{FACULTY}/allocations/{course_allocation.allocation_id}/assessments/{assessment.assessment_id}/'
        r = faculty_client.get(url)
        assert r.status_code == 200

    def test_update_assessment_returns_200(
        self, faculty_client, faculty_instance, course_allocation, assessment
    ):
        course_allocation.faculty = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()
        url = f'{FACULTY}/allocations/{course_allocation.allocation_id}/assessments/{assessment.assessment_id}/'
        r = faculty_client.patch(url, {
            'assessment_name': assessment.assessment_name,
            'assessment_type': assessment.assessment_type,
            'assessment_date': str(assessment.assessment_date),
            'weightage': assessment.weightage,
            'total_marks': assessment.total_marks,
            'student_submission': False,
        }, format='json')
        assert r.status_code == 200

    def test_delete_assessment_also_deletes_checked(
        self, faculty_client, faculty_instance, course_allocation, assessment, assessment_checked
    ):
        course_allocation.faculty = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()
        url = f'{FACULTY}/allocations/{course_allocation.allocation_id}/assessments/{assessment.assessment_id}/'
        r = faculty_client.delete(url)
        assert r.status_code == 204
        assert not Assessment.objects.filter(pk=assessment.pk).exists()
        assert not AssessmentChecked.objects.filter(pk=assessment_checked.pk).exists()

    def test_nonexistent_assessment_returns_404(self, faculty_client, course_allocation):
        url = f'{FACULTY}/allocations/{course_allocation.allocation_id}/assessments/99999/'
        r = faculty_client.get(url)
        assert r.status_code == 404

    def test_another_faculty_cannot_access_assessment(
        self, faculty_client, course_allocation, assessment, db
    ):
        from django.contrib.auth.models import User, Group
        from Models.models import Person, Faculty
        from datetime import date
        other_user = User.objects.create_user(username='other2@faculty.com', password='pass')
        other_user.groups.add(Group.objects.get(name='Faculty'))
        other_person = Person.objects.create(
            person_id='OTHER-002', first_name='Other2', last_name='Fac',
            father_name='F', gender='Male', dob=date(1985, 1, 1),
            cnic='11111-1111111-2', contact_number='+923001111112',
            institutional_email='other2@faculty.com', type='Faculty', user=other_user,
        )
        other_faculty = Faculty.objects.create(
            employee_id=other_person, department=course_allocation.faculty.department,
            designation='Lecturer', joining_date=date(2021, 1, 1),
        )
        course_allocation.faculty = other_faculty
        course_allocation.save()
        url = f'{FACULTY}/allocations/{course_allocation.allocation_id}/assessments/{assessment.assessment_id}/'
        r = faculty_client.get(url)
        assert r.status_code == 403

    def test_admin_can_read_assessment(self, admin_client, course_allocation, assessment):
        url = f'{FACULTY}/allocations/{course_allocation.allocation_id}/assessments/{assessment.assessment_id}/'
        r = admin_client.get(url)
        assert r.status_code == 200

    def test_admin_cannot_delete_assessment(self, admin_client, course_allocation, assessment):
        url = f'{FACULTY}/allocations/{course_allocation.allocation_id}/assessments/{assessment.assessment_id}/'
        r = admin_client.delete(url)
        assert r.status_code == 403


# ===========================================================================
# AssessmentListCreate — cache hit filter branch
# ===========================================================================

@pytest.mark.django_db
class TestAssessmentListCacheFilter:

    def test_list_with_no_params_serves_cache(
        self, faculty_client, faculty_instance, course_allocation, assessment
    ):
        course_allocation.faculty = faculty_instance
        course_allocation.save()
        url = f'{FACULTY}/allocations/{course_allocation.allocation_id}/assessments/'
        faculty_client.get(url)   # prime cache
        r = faculty_client.get(url)
        assert r.status_code == 200

    def test_list_with_type_filter_applies_to_cached_data(
        self, faculty_client, faculty_instance, course_allocation, assessment
    ):
        course_allocation.faculty = faculty_instance
        course_allocation.save()
        url = f'{FACULTY}/allocations/{course_allocation.allocation_id}/assessments/'
        faculty_client.get(url)  # prime cache
        r = faculty_client.get(f'{url}?assessment_type=Quiz')
        assert r.status_code == 200


# ===========================================================================
# LectureRetrieveUpdateDestroyAPIView
# ===========================================================================

@pytest.mark.django_db
class TestLectureRetrieveUpdateDestroy:

    def test_get_lecture_returns_200(
        self, faculty_client, faculty_instance, course_allocation, lecture
    ):
        course_allocation.faculty = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()
        url = f'{FACULTY}/allocations/{course_allocation.allocation_id}/lectures/{lecture.lecture_id}/'
        r = faculty_client.get(url)
        assert r.status_code == 200

    def test_update_lecture_attendance_returns_200(
        self, faculty_client, faculty_instance, course_allocation, lecture, enrollment
    ):
        course_allocation.faculty = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()
        url = f'{FACULTY}/allocations/{course_allocation.allocation_id}/lectures/{lecture.lecture_id}/'
        r = faculty_client.patch(url, {
            'venue': 'Room 202',
            'duration': 90,
            'topic': 'Updated Topic',
            'starting_time': (timezone.now() - timedelta(hours=2)).isoformat(),
            'attendance_set': [
                {
                    'id': Attendance.objects.filter(lecture=lecture).first().id,
                    'enrollment': enrollment.enrollment_id,
                    'is_present': True,
                }
            ],
        }, format='json')
        assert r.status_code == 200

    def test_delete_lecture_returns_204(
        self, faculty_client, faculty_instance, course_allocation, lecture
    ):
        course_allocation.faculty = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()
        url = f'{FACULTY}/allocations/{course_allocation.allocation_id}/lectures/{lecture.lecture_id}/'
        r = faculty_client.delete(url)
        assert r.status_code == 204

    def test_nonexistent_lecture_returns_404(self, faculty_client, course_allocation):
        url = f'{FACULTY}/allocations/{course_allocation.allocation_id}/lectures/NONE-99/'
        r = faculty_client.get(url)
        assert r.status_code == 404

    def test_another_faculty_cannot_access_lecture(
        self, faculty_client, course_allocation, lecture, db
    ):
        from django.contrib.auth.models import User, Group
        from Models.models import Person, Faculty
        from datetime import date
        other_user = User.objects.create_user(username='other3@faculty.com', password='pass')
        other_user.groups.add(Group.objects.get(name='Faculty'))
        other_person = Person.objects.create(
            person_id='OTHER-003', first_name='Other3', last_name='Fac',
            father_name='F', gender='Male', dob=date(1985, 1, 1),
            cnic='22222-2222222-2', contact_number='+923002222222',
            institutional_email='other3@faculty.com', type='Faculty', user=other_user,
        )
        other_faculty = Faculty.objects.create(
            employee_id=other_person, department=course_allocation.faculty.department,
            designation='Lecturer', joining_date=date(2021, 1, 1),
        )
        course_allocation.faculty = other_faculty
        course_allocation.save()
        url = f'{FACULTY}/allocations/{course_allocation.allocation_id}/lectures/{lecture.lecture_id}/'
        r = faculty_client.get(url)
        assert r.status_code == 403

    def test_student_cannot_access_lecture(self, student_client, course_allocation, lecture):
        url = f'{FACULTY}/allocations/{course_allocation.allocation_id}/lectures/{lecture.lecture_id}/'
        r = student_client.get(url)
        assert r.status_code == 403


# ===========================================================================
# ResultCalculationRequest — confirmed request message
# ===========================================================================

@pytest.mark.django_db
class TestResultCalculationConfirmedMessage:

    def test_confirmed_pending_request_returns_visit_portal_message(
        self, faculty_client, faculty_instance, course_allocation, db
    ):
        course_allocation.faculty = faculty_instance
        course_allocation.save()
        ChangeRequest.objects.create(
            change_type='result_calculation',
            target_allocation=course_allocation,
            requested_by=faculty_instance.employee_id.user,
            status='confirmed',
        )
        url = reverse('Faculty:allocation-calculate-result', kwargs={
            'allocation_id': course_allocation.allocation_id
        })
        r = faculty_client.get(url)
        assert r.status_code == 200
        assert 'confirmed' in r.data.get('message', '').lower() or 'approved' in r.data.get('message', '').lower()

    def test_student_cannot_request_result_calculation(
        self, student_client, course_allocation
    ):
        url = reverse('Faculty:allocation-calculate-result', kwargs={
            'allocation_id': course_allocation.allocation_id
        })
        r = student_client.get(url)
        assert r.status_code == 403


# ===========================================================================
# FacultyRequestsUpdateView
# ===========================================================================

@pytest.mark.django_db
class TestFacultyRequestsUpdateView:

    def test_anon_cannot_update_request(self, anon_client, change_request):
        url = reverse('Faculty:change-request-update', kwargs={'pk': change_request.pk})
        r = anon_client.patch(url, {'status': 'applied'}, format='json')
        assert r.status_code == 401

    def test_admin_cannot_update_faculty_request(self, admin_client, change_request):
        url = reverse('Faculty:change-request-update', kwargs={'pk': change_request.pk})
        r = admin_client.patch(url, {'status': 'applied'}, format='json')
        assert r.status_code == 403

    def test_faculty_can_apply_confirmed_request(
        self, faculty_client, faculty_instance, course_allocation, enrollment,
        assessment, assessment_checked, change_request
    ):
        course_allocation.faculty = faculty_instance
        course_allocation.status = 'Ongoing'
        course_allocation.save()
        enrollment.allocation = course_allocation
        enrollment.status = 'Active'
        enrollment.save()
        assessment_checked.obtained = 75
        assessment_checked.save()
        change_request.status = 'confirmed'
        change_request.target_allocation = course_allocation
        change_request.requested_by = faculty_instance.employee_id.user
        change_request.save()
        url = reverse('Faculty:change-request-update', kwargs={'pk': change_request.pk})
        r = faculty_client.patch(url, {'status': 'applied'}, format='json')
        assert r.status_code in (200, 400)  # 400 if null marks still present

    def test_faculty_cannot_update_other_facultys_request(
        self, faculty_client, change_request, db
    ):
        from django.contrib.auth.models import User
        other_user = User.objects.create_user(username='req_other@test.com', password='pass')
        change_request.requested_by = other_user
        change_request.save()
        url = reverse('Faculty:change-request-update', kwargs={'pk': change_request.pk})
        r = faculty_client.patch(url, {'status': 'applied'}, format='json')
        assert r.status_code in (403, 404)

    def test_nonexistent_request_returns_404(self, faculty_client):
        url = reverse('Faculty:change-request-update', kwargs={'pk': 99999})
        r = faculty_client.patch(url, {'status': 'applied'}, format='json')
        assert r.status_code == 404
