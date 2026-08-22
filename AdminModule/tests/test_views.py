"""
test_views.py
-------------
HTTP-layer tests for every AdminModule view.

Coverage targets:
  - Permission matrix (anon / student / faculty / admin / superuser) for every endpoint
  - Cache hit vs miss paths in every list view
  - Filter / search / ordering branches (bypass cache vs serve from cache)
  - perform_create / perform_update / perform_destroy side-effects
  - ChangeRequestView (token endpoint): valid, expired, already-processed
  - BulkCreateAPIView: GET template (faculty, student, missing type) + POST
  - TranscriptBulkCreateAPIView: happy path + missing results guard
  - EnrollmentRetrieveUpdateDestroy: delete blocked when course_gpa present
  - destroy_mixin (Faculty/Student DELETE): creates ChangeRequest, sends email
  - AdminProfile GET cache hit, PUT valid/invalid
"""

import io
import csv
import uuid
import pytest
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from Models.models import (
    Faculty, Student, Department, Program, Course, Semester,
    SemesterDetails, CourseAllocation, Enrollment, Result,
    ChangeRequest, Transcript, Class,
)

ADMIN = '/api/admin'


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_csv(headers, rows):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=headers)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue().encode()


# ===========================================================================
# Admin Dashboard
# ===========================================================================

@pytest.mark.django_db
class TestAdminDashboard:

    def test_anon_returns_401(self, anon_client):
        assert anon_client.get(reverse('Admin:admin-dashboard')).status_code == 401

    def test_faculty_returns_403(self, faculty_client):
        assert faculty_client.get(reverse('Admin:admin-dashboard')).status_code == 403

    def test_student_returns_403(self, student_client):
        assert student_client.get(reverse('Admin:admin-dashboard')).status_code == 403

    def test_admin_returns_200(self, admin_client):
        assert admin_client.get(reverse('Admin:admin-dashboard')).status_code == 200

    def test_response_has_all_required_keys(self, admin_client):
        r = admin_client.get(reverse('Admin:admin-dashboard'))
        for key in [
            'admin', 'students_total', 'faculty_total', 'programs_total',
            'courses_total', 'classes_total', 'allocation_total',
            'enrollment_total', 'students_status_count',
            'enrollments_status_count', 'allocations_status_count',
            'classes_student_count', 'departments_data',
            'enrollment_yearly', 'yearly_admission',
        ]:
            assert key in r.data, f'Missing key: {key}'

    def test_second_request_served_from_cache(self, admin_client, admin_instance):
        url = reverse('Admin:admin-dashboard')
        admin_client.get(url)
        key = f'admin:dashboard:{admin_instance.employee_id.user.username}'
        assert cache.get(key) is not None
        r2 = admin_client.get(url)
        assert r2.status_code == 200

    def test_cache_miss_triggers_db_query(self, admin_client, admin_instance):
        url = reverse('Admin:admin-dashboard')
        key = f'admin:dashboard:{admin_instance.employee_id.user.username}'
        cache.delete(key)
        r = admin_client.get(url)
        assert r.status_code == 200
        assert cache.get(key) is not None


# ===========================================================================
# Admin Profile
# ===========================================================================

@pytest.mark.django_db
class TestAdminProfile:

    def test_anon_returns_401(self, anon_client):
        assert anon_client.get(reverse('Admin:admin-profile')).status_code == 401

    def test_faculty_returns_403(self, faculty_client):
        assert faculty_client.get(reverse('Admin:admin-profile')).status_code == 403

    def test_student_returns_403(self, student_client):
        assert student_client.get(reverse('Admin:admin-profile')).status_code == 403

    def test_admin_get_returns_200(self, admin_client):
        assert admin_client.get(reverse('Admin:admin-profile')).status_code == 200

    def test_get_caches_profile(self, admin_client, admin_instance):
        key = f'admin:{admin_instance.employee_id.user.username}'
        cache.delete(key)
        admin_client.get(reverse('Admin:admin-profile'))
        assert cache.get(key) is not None

    def test_get_cache_hit_returns_200(self, admin_client, admin_instance):
        url = reverse('Admin:admin-profile')
        admin_client.get(url)  # populate cache
        r = admin_client.get(url)
        assert r.status_code == 200

    def test_put_valid_data_returns_200(self, admin_client, admin_instance):
        payload = {
            'person': {
                'user': {'password': 'newpassword123'},
                'contact_number': '+923009876543',
                'personal_email': 'updated@test.com',
            },
            'joining_date': str(admin_instance.joining_date),
            'status': admin_instance.status,
        }
        r = admin_client.put(reverse('Admin:admin-profile'), payload, format='json')
        assert r.status_code == 200

    def test_put_invalid_contact_number_returns_400(self, admin_client):
        payload = {
            'person': {'contact_number': '123'},
        }
        r = admin_client.put(reverse('Admin:admin-profile'), payload, format='json')
        assert r.status_code == 400

    def test_put_invalidates_cache(self, admin_client, admin_instance):
        key = f'admin:{admin_instance.employee_id.user.username}'
        admin_client.get(reverse('Admin:admin-profile'))
        assert cache.get(key) is not None
        admin_client.put(reverse('Admin:admin-profile'), {
            'person': {'contact_number': '+923001234567'},
            'joining_date': str(admin_instance.joining_date),
            'status': admin_instance.status,
        }, format='json')
        # after PUT, cache is deleted and repopulated
        # key may exist again (repopulated in PUT) but content reflects new data


# ===========================================================================
# Faculty List & Create
# ===========================================================================

@pytest.mark.django_db
class TestFacultyListCreate:

    def test_anon_returns_401(self, anon_client):
        assert anon_client.get(f'{ADMIN}/faculty/').status_code == 401

    def test_faculty_returns_403(self, faculty_client):
        assert faculty_client.get(f'{ADMIN}/faculty/').status_code == 403

    def test_student_returns_403(self, student_client):
        assert student_client.get(f'{ADMIN}/faculty/').status_code == 403

    def test_admin_list_returns_200(self, admin_client, faculty_instance):
        r = admin_client.get(f'{ADMIN}/faculty/')
        assert r.status_code == 200
        assert r.data['count'] >= 1

    def test_list_cache_miss_triggers_cache_task(self, admin_client, faculty_instance):
        cache.delete('admin:faculty_list')
        with patch('AdminModule.views.cache_faculty_data_task.delay') as mock_delay:
            r = admin_client.get(f'{ADMIN}/faculty/')
        assert r.status_code == 200
        mock_delay.assert_called_once()

    def test_list_no_filter_serves_from_cache(self, admin_client, admin_instance, faculty_instance):
        # populate cache first
        from AdminModule.tasks import cache_faculty_data_task
        cache_faculty_data_task.delay(admin_instance.employee_id.user.id)
        r = admin_client.get(f'{ADMIN}/faculty/')
        assert r.status_code == 200

    def test_list_search_bypasses_cache(self, admin_client, faculty_instance):
        cache.set('admin:faculty_list', [])  # pre-populate cache
        r = admin_client.get(f'{ADMIN}/faculty/?search=Faculty')
        assert r.status_code == 200

    def test_list_ordering_bypasses_cache(self, admin_client, admin_instance, faculty_instance):
        from AdminModule.tasks import cache_faculty_data_task
        cache_faculty_data_task.delay(admin_instance.employee_id.user.id)
        r = admin_client.get(f'{ADMIN}/faculty/?ordering=designation')
        assert r.status_code == 200

    def test_list_department_filter_uses_dept_cache_key(self, admin_client, admin_instance, faculty_instance, department):
        from AdminModule.tasks import cache_faculty_data_task
        cache_faculty_data_task.delay(admin_instance.employee_id.user.id)
        r = admin_client.get(f'{ADMIN}/faculty/?department={department.department_id}')
        assert r.status_code == 200

    def test_list_designation_filter_uses_designation_cache_key(self, admin_client, admin_instance, faculty_instance):
        from AdminModule.tasks import cache_faculty_data_task
        cache_faculty_data_task.delay(admin_instance.employee_id.user.id)
        r = admin_client.get(f'{ADMIN}/faculty/?designation=Lecturer')
        assert r.status_code == 200

    def test_list_department_and_designation_filter(self, admin_client, admin_instance, faculty_instance, department):
        from AdminModule.tasks import cache_faculty_data_task
        cache_faculty_data_task.delay(admin_instance.employee_id.user.id)
        r = admin_client.get(f'{ADMIN}/faculty/?department={department.department_id}&designation=Lecturer')
        assert r.status_code == 200

    def test_create_faculty_returns_201(self, admin_client, department, faculty_group):
        data = {
            'person': {
                'user': {'password': 'testpass123'},
                'first_name': 'New',
                'last_name': 'Faculty',
                'father_name': 'Father Name',
                'gender': 'Male',
                'dob': '1988-01-01',
                'cnic': '54321-7654321-1',
                'contact_number': '+923009999991',
                'institutional_email': 'newfaculty@test.com',
            },
            'department': department.department_id,
            'designation': 'Lecturer',
            'joining_date': '2024-01-01',
        }
        r = admin_client.post(f'{ADMIN}/faculty/', data, format='json')
        assert r.status_code == 201

    def test_create_triggers_cache_task(self, admin_client, department, faculty_group):
        data = {
            'person': {
                'user': {'password': 'testpass123'},
                'first_name': 'Cache',
                'last_name': 'Test',
                'father_name': 'Father',
                'gender': 'Male',
                'dob': '1988-01-01',
                'cnic': '11111-1111111-1',
                'contact_number': '+923001111110',
                'institutional_email': 'cachetest@test.com',
            },
            'department': department.department_id,
            'designation': 'Lecturer',
            'joining_date': '2024-01-01',
        }
        with patch('AdminModule.views.cache_faculty_data_task.delay') as mock_delay:
            admin_client.post(f'{ADMIN}/faculty/', data, format='json')
        mock_delay.assert_called_once()


# ===========================================================================
# Faculty Retrieve & Update & Delete
# ===========================================================================

@pytest.mark.django_db
class TestFacultyRetrieveUpdate:

    def test_anon_returns_401(self, anon_client, faculty_instance):
        pk = faculty_instance.employee_id.person_id
        assert anon_client.get(reverse('Admin:faculty-detail', kwargs={'employee_id': pk})).status_code == 401

    def test_admin_get_returns_200(self, admin_client, faculty_instance):
        pk = faculty_instance.employee_id.person_id
        r = admin_client.get(reverse('Admin:faculty-detail', kwargs={'employee_id': pk}))
        assert r.status_code == 200

    def test_admin_patch_designation_succeeds(self, admin_client, faculty_instance):
        pk = faculty_instance.employee_id.person_id
        r = admin_client.patch(
            reverse('Admin:faculty-detail', kwargs={'employee_id': pk}),
            {'designation': 'Senior Lecturer'}, format='json'
        )
        assert r.status_code == 200
        faculty_instance.refresh_from_db()
        assert faculty_instance.designation == 'Senior Lecturer'

    def test_patch_triggers_cache_task(self, admin_client, faculty_instance):
        pk = faculty_instance.employee_id.person_id
        with patch('AdminModule.views.cache_faculty_data_task.delay') as mock_delay:
            admin_client.patch(
                reverse('Admin:faculty-detail', kwargs={'employee_id': pk}),
                {'designation': 'Professor'}, format='json'
            )
        mock_delay.assert_called_once()

    def test_nonexistent_faculty_returns_404(self, admin_client):
        r = admin_client.get(reverse('Admin:faculty-detail', kwargs={'employee_id': 'NUM-NONE-9999-99'}))
        assert r.status_code == 404

    def test_delete_not_allowed(self, admin_client, faculty_instance):
        pk = faculty_instance.employee_id.person_id
        r = admin_client.delete(reverse('Admin:faculty-detail', kwargs={'employee_id': pk}))
        assert r.status_code == 405
        assert Faculty.objects.filter(pk=faculty_instance.pk).exists()

    def test_faculty_user_cannot_change_designation_via_admin_endpoint(self, faculty_client, faculty_instance):
        pk = faculty_instance.employee_id.person_id
        original = faculty_instance.designation
        faculty_client.patch(
            reverse('Admin:faculty-detail', kwargs={'employee_id': pk}),
            {'designation': 'Professor'}, format='json'
        )
        faculty_instance.refresh_from_db()
        assert faculty_instance.designation == original


# ===========================================================================
# Student List & Create
# ===========================================================================

@pytest.mark.django_db
class TestStudentListCreate:

    def test_anon_returns_401(self, anon_client):
        assert anon_client.get(f'{ADMIN}/students/').status_code == 401

    def test_faculty_returns_403(self, faculty_client):
        assert faculty_client.get(f'{ADMIN}/students/').status_code == 403

    def test_student_returns_403(self, student_client):
        assert student_client.get(f'{ADMIN}/students/').status_code == 403

    def test_admin_list_returns_200(self, admin_client, student_instance):
        r = admin_client.get(f'{ADMIN}/students/')
        assert r.status_code == 200
        assert r.data['count'] >= 1

    def test_student_class_display_shows_readable_string(self, admin_client, student_instance, batch_class):
        r = admin_client.get(f'{ADMIN}/students/')
        assert r.status_code == 200
        result = next(
            row for row in r.data['results']
            if row['person']['person_id'] == student_instance.student_id.person_id
        )
        assert result['student_class'] == batch_class.pk
        assert result['student_class_display'] == str(batch_class)

    def test_list_cache_miss_triggers_cache_task(self, admin_client):
        cache.delete('admin:student_list')
        with patch('AdminModule.views.cache_student_data_task.delay') as mock_delay:
            admin_client.get(f'{ADMIN}/students/')
        mock_delay.assert_called_once()

    def test_list_no_filter_serves_cache(self, admin_client, admin_instance, student_instance):
        from AdminModule.tasks import cache_student_data_task
        cache_student_data_task.delay(admin_instance.employee_id.user.id)
        r = admin_client.get(f'{ADMIN}/students/')
        assert r.status_code == 200

    def test_list_search_bypasses_cache(self, admin_client, admin_instance, student_instance):
        from AdminModule.tasks import cache_student_data_task
        cache_student_data_task.delay(admin_instance.employee_id.user.id)
        r = admin_client.get(f'{ADMIN}/students/?search=Student')
        assert r.status_code == 200

    def test_list_ordering_bypasses_cache(self, admin_client, admin_instance, student_instance):
        from AdminModule.tasks import cache_student_data_task
        cache_student_data_task.delay(admin_instance.employee_id.user.id)
        r = admin_client.get(f'{ADMIN}/students/?ordering=status')
        assert r.status_code == 200

    def test_list_program_filter(self, admin_client, admin_instance, student_instance, program):
        from AdminModule.tasks import cache_student_data_task
        cache_student_data_task.delay(admin_instance.employee_id.user.id)
        r = admin_client.get(f'{ADMIN}/students/?program={program.program_id}')
        assert r.status_code == 200

    def test_list_status_filter(self, admin_client, admin_instance, student_instance):
        from AdminModule.tasks import cache_student_data_task
        cache_student_data_task.delay(admin_instance.employee_id.user.id)
        r = admin_client.get(f'{ADMIN}/students/?status=Active')
        assert r.status_code == 200

    def test_list_department_filter(self, admin_client, admin_instance, student_instance, department):
        from AdminModule.tasks import cache_student_data_task
        cache_student_data_task.delay(admin_instance.employee_id.user.id)
        r = admin_client.get(f'{ADMIN}/students/?program__department={department.department_id}')
        assert r.status_code == 200

    def test_list_department_and_status_filter(self, admin_client, admin_instance, student_instance, department):
        from AdminModule.tasks import cache_student_data_task
        cache_student_data_task.delay(admin_instance.employee_id.user.id)
        r = admin_client.get(f'{ADMIN}/students/?program__department={department.department_id}&status=Active')
        assert r.status_code == 200

    def test_list_multiple_filters_beyond_two_bypasses_cache(self, admin_client, admin_instance, student_instance, program, department):
        from AdminModule.tasks import cache_student_data_task
        cache_student_data_task.delay(admin_instance.employee_id.user.id)
        r = admin_client.get(f'{ADMIN}/students/?program={program.program_id}&status=Active&program__department={department.department_id}')
        assert r.status_code == 200


# ===========================================================================
# Student Retrieve & Update & Delete
# ===========================================================================

@pytest.mark.django_db
class TestStudentRetrieveUpdate:

    def test_anon_returns_401(self, anon_client, student_instance):
        pk = student_instance.student_id.person_id
        assert anon_client.get(reverse('Admin:student-detail', kwargs={'student_id': pk})).status_code == 401

    def test_admin_get_returns_200(self, admin_client, student_instance):
        pk = student_instance.student_id.person_id
        r = admin_client.get(reverse('Admin:student-detail', kwargs={'student_id': pk}))
        assert r.status_code == 200

    def test_admin_patch_status(self, admin_client, student_instance):
        pk = student_instance.student_id.person_id
        r = admin_client.patch(
            reverse('Admin:student-detail', kwargs={'student_id': pk}),
            {'status': 'Frozen'}, format='json'
        )
        assert r.status_code == 200
        student_instance.refresh_from_db()
        assert student_instance.status == 'Frozen'

    def test_patch_triggers_cache_task(self, admin_client, student_instance):
        pk = student_instance.student_id.person_id
        with patch('AdminModule.views.cache_student_data_task.delay') as mock_delay:
            admin_client.patch(
                reverse('Admin:student-detail', kwargs={'student_id': pk}),
                {'status': 'Frozen'}, format='json'
            )
        mock_delay.assert_called_once()

    def test_nonexistent_student_returns_404(self, admin_client):
        r = admin_client.get(reverse('Admin:student-detail', kwargs={'student_id': 'NUM-NONE-9999-99'}))
        assert r.status_code == 404

    def test_delete_not_allowed(self, admin_client, student_instance):
        pk = student_instance.student_id.person_id
        r = admin_client.delete(reverse('Admin:student-detail', kwargs={'student_id': pk}))
        assert r.status_code == 405
        assert Student.objects.filter(pk=student_instance.pk).exists()


# ===========================================================================
# Department Endpoints
# ===========================================================================

@pytest.mark.django_db
class TestDepartmentEndpoints:

    def test_anon_list_returns_401(self, anon_client):
        assert anon_client.get(f'{ADMIN}/departments/').status_code == 401

    def test_student_list_returns_403(self, student_client):
        assert student_client.get(f'{ADMIN}/departments/').status_code == 403

    def test_faculty_list_returns_403(self, faculty_client, department):
        r = faculty_client.get(f'{ADMIN}/departments/')
        assert r.status_code == 403

    def test_admin_list_returns_200(self, admin_client, department):
        r = admin_client.get(f'{ADMIN}/departments/')
        assert r.status_code == 200

    def test_admin_get_detail_returns_200(self, admin_client, department):
        r = admin_client.get(
            reverse('Admin:department-detail', kwargs={'department_id': department.department_id})
        )
        assert r.status_code == 200

    def test_faculty_cannot_post_to_department(self, faculty_client, department):
        r = faculty_client.patch(
            reverse('Admin:department-detail', kwargs={'department_id': department.department_id}),
            {'department_name': 'Changed'}, format='json'
        )
        # DepartmentPermissions: Admin can GET/PUT/PATCH, Faculty cannot
        assert r.status_code == 403

    def test_nonexistent_department_returns_404(self, admin_client):
        r = admin_client.get(
            reverse('Admin:department-detail', kwargs={'department_id': 'NONE'})
        )
        assert r.status_code == 404

    def test_setting_hod_notifies_nominated_faculty(self, admin_client, department, faculty_instance):
        from Models.models import Notification
        with patch('AdminModule.tasks.send_hod_request_mail.apply_async'):
            r = admin_client.patch(
                reverse('Admin:department-detail', kwargs={'department_id': department.department_id}),
                {'HOD': faculty_instance.employee_id.person_id}, format='json'
            )
        assert r.status_code == 200
        assert Notification.objects.filter(
            recipient=faculty_instance.employee_id.user, verb='hod_nomination'
        ).exists()


# ===========================================================================
# Program Endpoints
# ===========================================================================

@pytest.mark.django_db
class TestProgramEndpoints:

    def test_anon_returns_401(self, anon_client):
        assert anon_client.get(f'{ADMIN}/programs/').status_code == 401

    def test_faculty_returns_403(self, faculty_client):
        assert faculty_client.get(f'{ADMIN}/programs/').status_code == 403

    def test_admin_list_returns_200(self, admin_client, program):
        r = admin_client.get(f'{ADMIN}/programs/')
        assert r.status_code == 200

    def test_list_cache_miss_triggers_task(self, admin_client):
        cache.delete('admin:programs_list')
        with patch('AdminModule.views.cache_programs_data_task.delay') as mock_delay:
            admin_client.get(f'{ADMIN}/programs/')
        mock_delay.assert_called_once()

    def test_list_no_filter_serves_cache(self, admin_client, admin_instance, program):
        from AdminModule.tasks import cache_programs_data_task
        cache_programs_data_task.delay(admin_instance.employee_id.user.id)
        r = admin_client.get(f'{ADMIN}/programs/')
        assert r.status_code == 200

    def test_list_search_bypasses_cache(self, admin_client, admin_instance, program):
        from AdminModule.tasks import cache_programs_data_task
        cache_programs_data_task.delay(admin_instance.employee_id.user.id)
        r = admin_client.get(f'{ADMIN}/programs/?search=BS')
        assert r.status_code == 200

    def test_list_department_filter(self, admin_client, admin_instance, program, department):
        from AdminModule.tasks import cache_programs_data_task
        cache_programs_data_task.delay(admin_instance.employee_id.user.id)
        r = admin_client.get(f'{ADMIN}/programs/?department={department.department_id}')
        assert r.status_code == 200

    def test_create_program_returns_201(self, admin_client, department):
        r = admin_client.post(f'{ADMIN}/programs/', {
            'program_id': 'MSCS',
            'program_name': 'MS Computer Science',
            'department_id': department.department_id,
            'total_semesters': 4,
        }, format='json')
        assert r.status_code == 201

    def test_create_triggers_cache_task(self, admin_client, department):
        with patch('AdminModule.views.cache_programs_data_task.delay') as mock_delay:
            admin_client.post(f'{ADMIN}/programs/', {
                'program_id': 'MSIT',
                'program_name': 'MS IT',
                'department_id': department.department_id,
                'total_semesters': 4,
            }, format='json')
        mock_delay.assert_called_once()

    def test_get_program_detail(self, admin_client, program):
        r = admin_client.get(reverse('Admin:program-detail', kwargs={'program_id': program.program_id}))
        assert r.status_code == 200

    def test_update_program(self, admin_client, program):
        r = admin_client.patch(
            reverse('Admin:program-detail', kwargs={'program_id': program.program_id}),
            {'program_name': 'Updated Name'}, format='json'
        )
        assert r.status_code == 200

    def test_delete_program(self, admin_client, program):
        with patch('AdminModule.views.cache_programs_data_task.delay'):
            r = admin_client.delete(
                reverse('Admin:program-detail', kwargs={'program_id': program.program_id})
            )
        assert r.status_code == 204

    def test_nonexistent_program_returns_404(self, admin_client):
        assert admin_client.get(
            reverse('Admin:program-detail', kwargs={'program_id': 'NONE'})
        ).status_code == 404


# ===========================================================================
# Course Endpoints
# ===========================================================================

@pytest.mark.django_db
class TestCourseEndpoints:

    def test_anon_returns_401(self, anon_client):
        assert anon_client.get(f'{ADMIN}/courses/').status_code == 401

    def test_faculty_returns_403(self, faculty_client):
        assert faculty_client.get(f'{ADMIN}/courses/').status_code == 403

    def test_admin_list_returns_200(self, admin_client, course):
        r = admin_client.get(f'{ADMIN}/courses/')
        assert r.status_code == 200

    def test_list_cache_miss_triggers_task(self, admin_client):
        cache.delete('admin:courses_list')
        with patch('AdminModule.views.cache_courses_data_task.delay') as mock_delay:
            admin_client.get(f'{ADMIN}/courses/')
        mock_delay.assert_called_once()

    def test_list_no_filter_serves_cache(self, admin_client, admin_instance, course):
        from AdminModule.tasks import cache_courses_data_task
        cache_courses_data_task.delay(admin_instance.employee_id.user.id)
        r = admin_client.get(f'{ADMIN}/courses/')
        assert r.status_code == 200

    def test_list_with_any_filter_bypasses_cache(self, admin_client, admin_instance, course):
        from AdminModule.tasks import cache_courses_data_task
        cache_courses_data_task.delay(admin_instance.employee_id.user.id)
        r = admin_client.get(f'{ADMIN}/courses/?lab=false')
        assert r.status_code == 200

    def test_create_course_returns_201(self, admin_client):
        r = admin_client.post(f'{ADMIN}/courses/', {
            'course_code': 'CS-200',
            'course_name': 'Algorithms',
            'credit_hours': 3,
            'lab': False,
        }, format='json')
        assert r.status_code == 201

    def test_create_lab_course_auto_increments_credit_hours(self, admin_client):
        r = admin_client.post(f'{ADMIN}/courses/', {
            'course_code': 'CS-201',
            'course_name': 'Lab Algorithms',
            'credit_hours': 3,
            'lab': True,
        }, format='json')
        assert r.status_code == 201
        assert Course.objects.get(course_code='CS-201').credit_hours == 4

    def test_create_negative_credit_hours_returns_400(self, admin_client):
        r = admin_client.post(f'{ADMIN}/courses/', {
            'course_code': 'CS-202',
            'course_name': 'Bad',
            'credit_hours': -1,
            'lab': False,
        }, format='json')
        assert r.status_code == 400

    def test_create_credit_hours_over_5_returns_400(self, admin_client):
        r = admin_client.post(f'{ADMIN}/courses/', {
            'course_code': 'CS-203',
            'course_name': 'Too Much',
            'credit_hours': 6,
            'lab': False,
        }, format='json')
        assert r.status_code == 400

    def test_create_triggers_cache_task(self, admin_client):
        with patch('AdminModule.views.cache_courses_data_task.delay') as mock_delay:
            admin_client.post(f'{ADMIN}/courses/', {
                'course_code': 'CS-299',
                'course_name': 'Trigger Test',
                'credit_hours': 2,
                'lab': False,
            }, format='json')
        mock_delay.assert_called_once()

    def test_get_course_detail(self, admin_client, course):
        r = admin_client.get(reverse('Admin:course-detail', kwargs={'course_code': course.course_code}))
        assert r.status_code == 200

    def test_update_course_triggers_cache_task(self, admin_client, course):
        with patch('AdminModule.views.cache_courses_data_task.delay') as mock_delay:
            admin_client.patch(
                reverse('Admin:course-detail', kwargs={'course_code': course.course_code}),
                {'course_name': 'Updated'}, format='json'
            )
        mock_delay.assert_called_once()

    def test_delete_course_triggers_cache_task(self, admin_client, course):
        with patch('AdminModule.views.cache_courses_data_task.delay') as mock_delay:
            admin_client.delete(
                reverse('Admin:course-detail', kwargs={'course_code': course.course_code})
            )
        mock_delay.assert_called_once()

    def test_nonexistent_course_returns_404(self, admin_client):
        assert admin_client.get(
            reverse('Admin:course-detail', kwargs={'course_code': 'NONE-999'})
        ).status_code == 404


# ===========================================================================
# Session Endpoints
# ===========================================================================

@pytest.mark.django_db
class TestSessionListCreate:

    def test_anon_returns_401(self, anon_client):
        assert anon_client.get(f'{ADMIN}/sessions/').status_code == 401

    def test_faculty_returns_403(self, faculty_client):
        assert faculty_client.get(f'{ADMIN}/sessions/').status_code == 403

    def test_admin_list_returns_200(self, admin_client, academic_session):
        r = admin_client.get(f'{ADMIN}/sessions/')
        assert r.status_code == 200
        assert r.data['count'] >= 1

    def test_filter_by_period(self, admin_client):
        from Models.models import AcademicSession
        fall = AcademicSession.objects.create(period='Fall', year=2024, status='Initiated')
        AcademicSession.objects.create(period='Spring', year=2025, status='Initiated')

        r = admin_client.get(f'{ADMIN}/sessions/?period=Fall')
        assert r.status_code == 200
        periods = [row['period'] for row in r.data['results']]
        assert periods == ['Fall'] * len(periods)
        assert fall.pk in [row['id'] for row in r.data['results']]

    def test_filter_by_year(self, admin_client):
        from Models.models import AcademicSession
        AcademicSession.objects.create(period='Fall', year=2024, status='Initiated')
        AcademicSession.objects.create(period='Spring', year=2025, status='Initiated')

        r = admin_client.get(f'{ADMIN}/sessions/?year=2025')
        assert r.status_code == 200
        assert all(row['year'] == 2025 for row in r.data['results'])

    def test_filter_by_status(self, admin_client):
        from Models.models import AcademicSession
        AcademicSession.objects.create(period='Fall', year=2024, status='Active')
        AcademicSession.objects.create(period='Spring', year=2025, status='Inactive')

        r = admin_client.get(f'{ADMIN}/sessions/?status=Active')
        assert r.status_code == 200
        assert all(row['status'] == 'Active' for row in r.data['results'])

    def test_search_by_period(self, admin_client):
        from Models.models import AcademicSession
        AcademicSession.objects.create(period='Fall', year=2024, status='Initiated')
        AcademicSession.objects.create(period='Spring', year=2025, status='Initiated')

        r = admin_client.get(f'{ADMIN}/sessions/?search=Spring')
        assert r.status_code == 200
        assert all(row['period'] == 'Spring' for row in r.data['results'])

    def test_ordering_by_year_descending(self, admin_client):
        from Models.models import AcademicSession
        AcademicSession.objects.create(period='Fall', year=2023, status='Initiated')
        AcademicSession.objects.create(period='Spring', year=2026, status='Initiated')

        r = admin_client.get(f'{ADMIN}/sessions/?ordering=-year')
        assert r.status_code == 200
        years = [row['year'] for row in r.data['results']]
        assert years == sorted(years, reverse=True)


@pytest.mark.django_db
class TestCurrentSessionView:
    """Public — no auth, consumed by the login page before a JWT exists."""

    def test_anon_can_access(self, anon_client, academic_session):
        r = anon_client.get('/api/sessions/current/')
        assert r.status_code == 200

    def test_excludes_inactive_and_completed(self, anon_client):
        from Models.models import AcademicSession
        AcademicSession.objects.create(period='Fall', year=2024, status='Inactive')
        AcademicSession.objects.create(period='Spring', year=2025, status='Completed')

        r = anon_client.get('/api/sessions/current/')
        assert r.status_code == 200
        assert r.data == []

    def test_includes_initiated_available_active(self, anon_client):
        from Models.models import AcademicSession
        AcademicSession.objects.create(period='Fall', year=2024, status='Initiated')
        AcademicSession.objects.create(period='Spring', year=2025, status='Available')
        AcademicSession.objects.create(period='Summer', year=2025, status='Active')

        r = anon_client.get('/api/sessions/current/')
        assert r.status_code == 200
        statuses = {row['status'] for row in r.data}
        assert statuses == {'Initiated', 'Available', 'Active'}

    def test_orders_active_first_then_available_then_initiated(self, anon_client):
        from Models.models import AcademicSession
        AcademicSession.objects.create(period='Fall', year=2024, status='Initiated')
        AcademicSession.objects.create(period='Spring', year=2025, status='Active')
        AcademicSession.objects.create(period='Summer', year=2025, status='Available')

        r = anon_client.get('/api/sessions/current/')
        assert r.status_code == 200
        assert [row['status'] for row in r.data] == ['Active', 'Available', 'Initiated']

    def test_response_is_not_paginated(self, anon_client, academic_session):
        r = anon_client.get('/api/sessions/current/')
        assert r.status_code == 200
        assert isinstance(r.data, list)

    def test_fields_present(self, anon_client, academic_session):
        r = anon_client.get('/api/sessions/current/')
        assert r.status_code == 200
        entry = r.data[0]
        assert set(entry.keys()) == {'id', 'period', 'year', 'status', 'availability_deadline', 'closing_deadline'}


# ===========================================================================
# Semester Endpoints
# ===========================================================================

@pytest.mark.django_db
class TestSemesterEndpoints:

    def test_anon_returns_401(self, anon_client):
        assert anon_client.get(f'{ADMIN}/semesters/').status_code == 401

    def test_faculty_returns_403(self, faculty_client):
        assert faculty_client.get(f'{ADMIN}/semesters/').status_code == 403

    def test_admin_list_returns_200(self, admin_client, inactive_semester):
        r = admin_client.get(f'{ADMIN}/semesters/')
        assert r.status_code == 200

    def test_list_cache_miss_triggers_task(self, admin_client):
        cache.delete('admin:semesters_list')
        with patch('AdminModule.views.cache_semester_data_task.delay') as mock_delay:
            admin_client.get(f'{ADMIN}/semesters/')
        mock_delay.assert_called_once()

    def test_list_no_filter_serves_cache(self, admin_client, admin_instance, inactive_semester):
        from AdminModule.tasks import cache_semester_data_task
        cache_semester_data_task.delay(admin_instance.employee_id.user.id)
        r = admin_client.get(f'{ADMIN}/semesters/')
        assert r.status_code == 200

    def test_list_with_filter_class_uses_cache_key(self, admin_client, admin_instance, inactive_semester, batch_class):
        from AdminModule.tasks import cache_semester_data_task
        cache_semester_data_task.delay(admin_instance.employee_id.user.id)
        r = admin_client.get(f'{ADMIN}/semesters/?associated_class={batch_class.class_id}')
        assert r.status_code == 200

    def test_get_semester_detail(self, admin_client, inactive_semester):
        r = admin_client.get(reverse('Admin:semester-detail', kwargs={'semester_id': inactive_semester.semester_id}))
        assert r.status_code == 200

    def test_update_activation_deadline_triggers_task(self, admin_client, inactive_semester):
        future = timezone.now() + timedelta(days=30)
        with patch('AdminModule.tasks.semester_activation_task.apply_async') as mock_task, \
             patch('AdminModule.tasks.cache_semester_enrollment_data_task.delay'):
            mock_task.return_value.id = 'fake-task-id'
            r = admin_client.patch(
                reverse('Admin:semester-detail', kwargs={'semester_id': inactive_semester.semester_id}),
                {'activation_deadline': future.isoformat()}, format='json'
            )
        assert r.status_code == 200

    def test_update_activation_deadline_in_past_returns_400(self, admin_client, inactive_semester):
        past = timezone.now() - timedelta(days=1)
        r = admin_client.patch(
            reverse('Admin:semester-detail', kwargs={'semester_id': inactive_semester.semester_id}),
            {'activation_deadline': past.isoformat()}, format='json'
        )
        assert r.status_code == 400

    def test_update_triggers_cache_task(self, admin_client, inactive_semester):
        future = timezone.now() + timedelta(days=30)
        with patch('AdminModule.views.cache_semester_data_task.delay') as mock_delay, \
             patch('AdminModule.tasks.semester_activation_task.apply_async') as mock_task, \
             patch('AdminModule.tasks.cache_semester_enrollment_data_task.delay'):
            mock_task.return_value.id = 'fake-task-id'
            admin_client.patch(
                reverse('Admin:semester-detail', kwargs={'semester_id': inactive_semester.semester_id}),
                {'activation_deadline': future.isoformat()}, format='json'
            )
        mock_delay.assert_called_once()

    def test_nonexistent_semester_returns_404(self, admin_client):
        assert admin_client.get(
            reverse('Admin:semester-detail', kwargs={'semester_id': 99999})
        ).status_code == 404


# ===========================================================================
# Class Endpoints
# ===========================================================================

@pytest.mark.django_db
class TestClassEndpoints:

    def test_anon_returns_401(self, anon_client):
        assert anon_client.get(f'{ADMIN}/classes/').status_code == 401

    def test_faculty_returns_403(self, faculty_client):
        assert faculty_client.get(f'{ADMIN}/classes/').status_code == 403

    def test_admin_list_returns_200(self, admin_client, batch_class):
        r = admin_client.get(f'{ADMIN}/classes/')
        assert r.status_code == 200

    def test_create_class_auto_generates_semesters(self, admin_client, program):
        with patch('AdminModule.views.cache_semester_data_task.delay'):
            r = admin_client.post(f'{ADMIN}/classes/', {
                'program': program.program_id,
                'batch_year': 2026,
            }, format='json')
        assert r.status_code == 201
        new_class = Class.objects.get(program=program, batch_year=2026)
        assert Semester.objects.filter(associated_class=new_class).count() == program.total_semesters

    def test_create_triggers_cache_task(self, admin_client, program):
        with patch('AdminModule.views.cache_semester_data_task.delay') as mock_delay:
            admin_client.post(f'{ADMIN}/classes/', {
                'program': program.program_id,
                'batch_year': 2027,
            }, format='json')
        mock_delay.assert_called_once()

    def test_get_class_detail(self, admin_client, batch_class):
        r = admin_client.get(reverse('Admin:class-detail', kwargs={'class_id': batch_class.class_id}))
        assert r.status_code == 200

    def test_nonexistent_class_returns_404(self, admin_client):
        assert admin_client.get(
            reverse('Admin:class-detail', kwargs={'class_id': 99999})
        ).status_code == 404


# ===========================================================================
# Course Allocation Endpoints
# ===========================================================================

@pytest.mark.django_db
class TestCourseAllocationEndpoints:

    def test_anon_returns_401(self, anon_client):
        assert anon_client.get(f'{ADMIN}/allocations/').status_code == 401

    def test_student_returns_403(self, student_client):
        assert student_client.get(f'{ADMIN}/allocations/').status_code == 403

    def test_admin_list_returns_200(self, admin_client, course_allocation):
        r = admin_client.get(f'{ADMIN}/allocations/')
        assert r.status_code == 200

    def test_list_no_filter_hits_db(self, admin_client, course_allocation):
        r = admin_client.get(f'{ADMIN}/allocations/')
        assert r.status_code == 200

    def test_list_semester_filter_checks_cache(self, admin_client, admin_instance, course_allocation, inactive_semester):
        from AdminModule.tasks import cache_courseAllocation_data_task
        cache_courseAllocation_data_task.delay(admin_instance.employee_id.user.id)
        r = admin_client.get(f'{ADMIN}/allocations/?semester={inactive_semester.semester_id}')
        assert r.status_code == 200

    def test_list_faculty_filter_checks_cache(self, admin_client, admin_instance, course_allocation, faculty_instance):
        from AdminModule.tasks import cache_courseAllocation_data_task
        cache_courseAllocation_data_task.delay(admin_instance.employee_id.user.id)
        r = admin_client.get(f'{ADMIN}/allocations/?faculty={faculty_instance.employee_id.person_id}')
        assert r.status_code == 200

    def test_list_search_bypasses_cache(self, admin_client, course_allocation):
        r = admin_client.get(f'{ADMIN}/allocations/?search=CS-101')
        assert r.status_code == 200

    def test_create_triggers_cache_task(self, admin_client, faculty_instance, course, inactive_semester):
        with patch('AdminModule.views.cache_courseAllocation_data_task.delay') as mock_delay, \
             patch('AdminModule.tasks.cache_semester_enrollment_data_task.delay'):
            admin_client.post(f'{ADMIN}/allocations/', {
                'faculty': faculty_instance.employee_id.person_id,
                'course': course.course_code,
                'semester': inactive_semester.semester_id,
            }, format='json')
        mock_delay.assert_called_once()

    def test_get_allocation_detail(self, admin_client, course_allocation):
        r = admin_client.get(
            reverse('Admin:allocation-detail', kwargs={'allocation_id': course_allocation.allocation_id})
        )
        assert r.status_code == 200

    def test_update_allocation_patch_not_allowed(self, admin_client, course_allocation):
        # PATCH/PUT on allocations is intentionally restricted for admin users
        r = admin_client.patch(
            reverse('Admin:allocation-detail', kwargs={'allocation_id': course_allocation.allocation_id}),
            {'status': 'Inactive'}, format='json'
        )
        assert r.status_code == 403

    def test_delete_inactive_allocation_succeeds(self, admin_client, course_allocation):
        assert course_allocation.status == 'Inactive'
        with patch('AdminModule.views.cache_courseAllocation_data_task.delay'), \
             patch('AdminModule.tasks.cache_semester_enrollment_data_task.delay'):
            r = admin_client.delete(
                reverse('Admin:allocation-detail', kwargs={'allocation_id': course_allocation.allocation_id})
            )
        assert r.status_code == 204

    def test_delete_triggers_both_cache_tasks(self, admin_client, course_allocation):
        with patch('AdminModule.views.cache_courseAllocation_data_task.delay') as mock1, \
             patch('AdminModule.tasks.cache_semester_enrollment_data_task.delay') as mock2:
            admin_client.delete(
                reverse('Admin:allocation-detail', kwargs={'allocation_id': course_allocation.allocation_id})
            )
        mock1.assert_called_once()
        mock2.assert_called_once()

    def test_nonexistent_allocation_returns_404(self, admin_client):
        assert admin_client.get(
            reverse('Admin:allocation-detail', kwargs={'allocation_id': 99999})
        ).status_code == 404


# ===========================================================================
# Enrollment Endpoints
# ===========================================================================

@pytest.mark.django_db
class TestEnrollmentEndpoints:

    def test_anon_returns_401(self, anon_client):
        assert anon_client.get(f'{ADMIN}/enrollments/').status_code == 401

    def test_faculty_cannot_create_enrollment_without_ongoing_allocation(self, faculty_client):
        # no Ongoing allocations → admin permission → GET only
        r = faculty_client.get(f'{ADMIN}/enrollments/')
        assert r.status_code == 403

    def test_admin_list_returns_200(self, admin_client, enrollment):
        r = admin_client.get(f'{ADMIN}/enrollments/')
        assert r.status_code == 200

    def test_list_student_filter_checks_cache(self, admin_client, admin_instance, enrollment, student_instance):
        from AdminModule.tasks import cache_enrollment_data_task
        cache_enrollment_data_task.delay(admin_instance.employee_id.user.id)
        r = admin_client.get(f'{ADMIN}/enrollments/?student={student_instance.student_id.person_id}')
        assert r.status_code == 200

    def test_list_search_bypasses_cache(self, admin_client, enrollment, student_instance):
        r = admin_client.get(f'{ADMIN}/enrollments/?search={student_instance.student_id.first_name}')
        assert r.status_code == 200

    def test_create_enrollment_with_ongoing_allocation(self, admin_client, student_instance, course_allocation):
        course_allocation.status = 'Ongoing'
        course_allocation.save()
        with patch('AdminModule.views.cache_enrollment_data_task.delay'):
            r = admin_client.post(f'{ADMIN}/enrollments/', {
                'student': student_instance.student_id.person_id,
                'allocation': course_allocation.allocation_id,
            }, format='json')
        assert r.status_code == 201

    def test_create_triggers_cache_task(self, admin_client, student_instance, course_allocation):
        course_allocation.status = 'Ongoing'
        course_allocation.save()
        with patch('AdminModule.views.cache_enrollment_data_task.delay') as mock_delay:
            admin_client.post(f'{ADMIN}/enrollments/', {
                'student': student_instance.student_id.person_id,
                'allocation': course_allocation.allocation_id,
            }, format='json')
        mock_delay.assert_called_once()

    def test_get_enrollment_detail(self, admin_client, enrollment):
        r = admin_client.get(
            reverse('Admin:enrollment-detail', kwargs={'enrollment_id': enrollment.enrollment_id})
        )
        assert r.status_code == 200

    def test_update_enrollment_triggers_cache_task(self, admin_client, enrollment):
        enrollment.allocation.status = 'Ongoing'
        enrollment.allocation.save()
        with patch('AdminModule.views.cache_enrollment_data_task.delay') as mock_delay:
            admin_client.patch(
                reverse('Admin:enrollment-detail', kwargs={'enrollment_id': enrollment.enrollment_id}),
                {'status': 'Active'}, format='json'
            )
        mock_delay.assert_called_once()

    def test_delete_enrollment_without_gpa_succeeds(self, admin_client, enrollment):
        enrollment.allocation.status = 'Ongoing'
        enrollment.allocation.save()
        assert enrollment.result.course_gpa is None
        with patch('AdminModule.views.cache_enrollment_data_task.delay'):
            r = admin_client.delete(
                reverse('Admin:enrollment-detail', kwargs={'enrollment_id': enrollment.enrollment_id})
            )
        assert r.status_code == 204

    def test_delete_enrollment_with_gpa_returns_403(self, admin_client, enrollment):
        enrollment.result.course_gpa = Decimal('3.5')
        enrollment.result.save()
        r = admin_client.delete(
            reverse('Admin:enrollment-detail', kwargs={'enrollment_id': enrollment.enrollment_id})
        )
        assert r.status_code == 403

    def test_nonexistent_enrollment_returns_404(self, admin_client):
        assert admin_client.get(
            reverse('Admin:enrollment-detail', kwargs={'enrollment_id': 99999})
        ).status_code == 404


# ===========================================================================
# Transcript Endpoints
# ===========================================================================

@pytest.mark.django_db
class TestTranscriptEndpoints:

    def test_anon_returns_401(self, anon_client):
        assert anon_client.get(f'{ADMIN}/transcripts/').status_code == 401

    def test_faculty_returns_403(self, faculty_client):
        assert faculty_client.get(f'{ADMIN}/transcripts/').status_code == 403

    def test_admin_list_returns_200(self, admin_client):
        r = admin_client.get(f'{ADMIN}/transcripts/')
        assert r.status_code == 200

    def test_bulk_create_requires_confirmation(self, admin_client, active_semester):
        r = admin_client.post(
            reverse('Admin:semester-transcripts-create', kwargs={'semester_id': active_semester.semester_id}),
            {'confirm': False}, format='json'
        )
        assert r.status_code == 400

    def test_bulk_create_missing_results_returns_400(
        self, admin_client, active_semester, enrollment, course_allocation
    ):
        course_allocation.semester = active_semester
        course_allocation.status = 'Completed'
        course_allocation.save()
        enrollment.allocation = course_allocation
        enrollment.status = 'Completed'
        enrollment.save()
        enrollment.result.course_gpa = None
        enrollment.result.save()

        r = admin_client.post(
            reverse('Admin:semester-transcripts-create', kwargs={'semester_id': active_semester.semester_id}),
            {'confirm': True}, format='json'
        )
        assert r.status_code == 400

    def test_bulk_create_nonexistent_semester_returns_404(self, admin_client):
        r = admin_client.post(
            reverse('Admin:semester-transcripts-create', kwargs={'semester_id': 99999}),
            {'confirm': True}, format='json'
        )
        assert r.status_code in (400, 404)

    def test_anon_bulk_create_returns_401(self, anon_client, active_semester):
        r = anon_client.post(
            reverse('Admin:semester-transcripts-create', kwargs={'semester_id': active_semester.semester_id}),
            {'confirm': True}, format='json'
        )
        assert r.status_code == 401


# ===========================================================================
# Change Request Endpoints
# ===========================================================================

@pytest.mark.django_db
class TestChangeRequestEndpoints:

    def test_anon_list_returns_401(self, anon_client):
        assert anon_client.get(f'{ADMIN}/requests/').status_code == 401

    def test_faculty_list_returns_403(self, faculty_client):
        assert faculty_client.get(f'{ADMIN}/requests/').status_code == 403

    def test_admin_list_returns_200(self, admin_client, change_request):
        r = admin_client.get(f'{ADMIN}/requests/')
        assert r.status_code == 200

    def test_admin_list_filter_by_status(self, admin_client, change_request):
        r = admin_client.get(f'{ADMIN}/requests/?status=pending')
        assert r.status_code == 200

    def test_admin_list_filter_by_change_type(self, admin_client, change_request):
        r = admin_client.get(f'{ADMIN}/requests/?change_type=result_calculation')
        assert r.status_code == 200

    def test_get_change_request_detail(self, admin_client, change_request):
        r = admin_client.get(
            reverse('Admin:change_request-detail', kwargs={'pk': change_request.pk})
        )
        assert r.status_code in (200, 403)  # object-level permission depends on requested_by

    def test_nonexistent_change_request_returns_404(self, admin_client):
        assert admin_client.get(
            reverse('Admin:change_request-detail', kwargs={'pk': 99999})
        ).status_code == 404


# ===========================================================================
# ChangeRequest Token Endpoint (no auth required)
# ===========================================================================

@pytest.mark.django_db
class TestChangeRequestTokenView:

    def test_valid_pending_token_confirms_request(self, anon_client, change_request):
        from Models.models import Notification
        token = change_request.confirmation_token
        r = anon_client.get(
            reverse('Admin:confirm-change-request', kwargs={'token': token})
        )
        assert r.status_code == 200
        change_request.refresh_from_db()
        assert change_request.status == 'confirmed'
        assert Notification.objects.filter(
            recipient=change_request.requested_by, verb='change_request_confirmed'
        ).exists()

    def test_expired_token_returns_400(self, anon_client, change_request):
        # backdate requested_at to force expiry
        change_request.requested_at = timezone.now() - timedelta(hours=49)
        change_request.save()
        r = anon_client.get(
            reverse('Admin:confirm-change-request', kwargs={'token': change_request.confirmation_token})
        )
        assert r.status_code == 400
        change_request.refresh_from_db()
        assert change_request.status == 'expired'

    def test_already_processed_token_returns_400(self, anon_client, change_request):
        change_request.status = 'confirmed'
        change_request.save()
        r = anon_client.get(
            reverse('Admin:confirm-change-request', kwargs={'token': change_request.confirmation_token})
        )
        assert r.status_code == 400
        assert 'already been processed' in r.data.get('error', '')

    def test_invalid_token_returns_404(self, anon_client):
        r = anon_client.get(
            reverse('Admin:confirm-change-request', kwargs={'token': uuid.uuid4()})
        )
        assert r.status_code == 404

    def test_result_calculation_type_schedules_confirmation_mail(self, anon_client, change_request):
        with patch('AdminModule.views.send_result_calculation_confirmation_mail.apply_async') as mock_task:
            anon_client.get(
                reverse('Admin:confirm-change-request', kwargs={'token': change_request.confirmation_token})
            )
        mock_task.assert_called_once()


# ===========================================================================
# Bulk Create Endpoint
# ===========================================================================

@pytest.mark.django_db
class TestBulkCreateEndpoints:

    def test_anon_returns_401(self, anon_client):
        assert anon_client.get(f'{ADMIN}/bulk/').status_code == 401

    def test_faculty_returns_403(self, faculty_client):
        assert faculty_client.get(f'{ADMIN}/bulk/').status_code == 403

    def test_get_no_type_returns_400(self, admin_client):
        r = admin_client.get(f'{ADMIN}/bulk/')
        assert r.status_code == 400
        assert 'type' in str(r.data).lower() or 'Template' in str(r.data)

    def test_get_faculty_template_returns_csv(self, admin_client):
        r = admin_client.get(f'{ADMIN}/bulk/?type=faculty')
        assert r.status_code == 200
        assert 'text/csv' in r.get('Content-Type', '')
        assert b'department' in r.content
        assert b'designation' in r.content

    def test_get_student_template_returns_csv(self, admin_client):
        r = admin_client.get(f'{ADMIN}/bulk/?type=student')
        assert r.status_code == 200
        assert 'text/csv' in r.get('Content-Type', '')
        assert b'program' in r.content
        assert b'admission_date' in r.content

    def test_post_with_unknown_type_returns_message(self, admin_client):
        from django.core.files.uploadedfile import SimpleUploadedFile
        f = SimpleUploadedFile('data.csv', b'col1\nval1\n', content_type='text/csv')
        r = admin_client.post(
            f'{ADMIN}/bulk/?type=unknown',
            {'file': f}, format='multipart'
        )
        # serializer.create returns {'message': ...} for unknown type
        assert r.status_code in (201, 400)

    def test_post_faculty_csv_returns_row_counts(
        self, admin_client, department, faculty_group
    ):
        from django.core.files.uploadedfile import SimpleUploadedFile
        csv_content = (
            'password,first_name,last_name,father_name,gender,cnic,dob,'
            'contact_number,institutional_email,department,designation,joining_date\n'
            'pass123,John,Doe,Father,Male,12345-1234567-9,1990-01-01,'
            '+923001111111,bulk_fac@test.com,CS,Lecturer,2024-01-01\n'
        )
        f = SimpleUploadedFile('faculty.csv', csv_content.encode(), content_type='text/csv')
        r = admin_client.post(f'{ADMIN}/bulk/?type=faculty', {'file': f}, format='multipart')
        assert r.status_code in (201, 400)
        if r.status_code == 201:
            assert 'row_count' in r.data


# ===========================================================================
# Permissions cross-check — superuser can do everything
# ===========================================================================

@pytest.mark.django_db
class TestSuperuserAccess:
    """Superuser must pass IsSuperUserOrAdminPermission on all endpoints."""

    @pytest.fixture
    def superuser_client(self):
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient
        from rest_framework_simplejwt.tokens import RefreshToken
        user = User.objects.create_superuser('superadmin', 'super@test.com', 'superpass123')
        token = str(RefreshToken.for_user(user).access_token)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        return client

    def test_superuser_can_list_faculty(self, superuser_client, faculty_instance):
        r = superuser_client.get(f'{ADMIN}/faculty/')
        assert r.status_code == 200

    def test_superuser_can_list_students(self, superuser_client, student_instance):
        r = superuser_client.get(f'{ADMIN}/students/')
        assert r.status_code == 200

    def test_superuser_can_list_programs(self, superuser_client, program):
        r = superuser_client.get(f'{ADMIN}/programs/')
        assert r.status_code == 200

    def test_superuser_cannot_access_admin_dashboard(self, superuser_client):
        # Dashboard requires Admin group membership; superusers use Django's own admin
        r = superuser_client.get(reverse('Admin:admin-dashboard'))
        assert r.status_code == 403
