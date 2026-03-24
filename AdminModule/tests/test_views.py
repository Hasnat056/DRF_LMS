import pytest
from django.urls import reverse

# Base path prefix — adjust if your urls.py mounts Admin under a different prefix
ADMIN = '/api/admin'


# ===========================================================================
# Admin Dashboard
# ===========================================================================

@pytest.mark.django_db
class TestAdminDashboard:

    def test_unauthenticated_returns_401(self, anon_client):
        response = anon_client.get(reverse('Admin:admin-dashboard'))
        assert response.status_code == 401

    def test_faculty_user_returns_403(self, faculty_client):
        response = faculty_client.get(reverse('Admin:admin-dashboard'))
        assert response.status_code == 403

    def test_student_user_returns_403(self, student_client):
        response = student_client.get(reverse('Admin:admin-dashboard'))
        assert response.status_code == 403

    def test_admin_user_returns_200(self, admin_client):
        response = admin_client.get(reverse('Admin:admin-dashboard'))
        assert response.status_code == 200

    def test_dashboard_response_has_required_keys(self, admin_client):
        response = admin_client.get(reverse('Admin:admin-dashboard'))
        assert response.status_code == 200
        expected_keys = [
            'admin', 'students_total', 'faculty_total', 'programs_total',
            'courses_total', 'classes_total', 'allocation_total',
            'enrollment_total', 'students_status_count',
            'enrollments_status_count', 'allocations_status_count',
        ]
        for key in expected_keys:
            assert key in response.data, f"Missing key: {key}"

    def test_dashboard_cached_on_second_request(self, admin_client):
        url = reverse('Admin:admin-dashboard')
        r1 = admin_client.get(url)
        r2 = admin_client.get(url)
        assert r1.status_code == r2.status_code == 200
        assert r1.data == r2.data


# ===========================================================================
# Faculty List & Create
# NOTE: faculty/ has no URL name, so we use the direct path
# ===========================================================================

@pytest.mark.django_db
class TestFacultyListCreate:

    def test_unauthenticated_returns_401(self, anon_client):
        response = anon_client.get(f'{ADMIN}/faculty/')
        assert response.status_code == 401

    def test_faculty_user_cannot_access_list(self, faculty_client):
        response = faculty_client.get(f'{ADMIN}/faculty/')
        assert response.status_code == 403

    def test_student_user_cannot_access_list(self, student_client):
        response = student_client.get(f'{ADMIN}/faculty/')
        assert response.status_code == 403

    def test_admin_can_list_faculty(self, admin_client, faculty_instance, faculty_group):
        response = admin_client.get(f'{ADMIN}/faculty/')
        assert response.status_code == 200
        assert response.data['count'] >= 1

    def test_admin_can_create_faculty(self, admin_client, department, faculty_group):
        data = {
            "person": {
                "user": {"password": "testpass123"},
                "first_name": "New",
                "last_name": "Faculty",
                "father_name": "Father Name",
                "gender": "Male",
                "dob": "1988-01-01",
                "cnic": "54321-7654321-1",
                "contact_number": "+923009999991",
                "institutional_email": "newfaculty@test.com",
            },
            "department_id": department.department_id,
            "designation": "Lecturer",
            "joining_date": "2024-01-01",
        }
        response = admin_client.post(f'{ADMIN}/faculty/', data, format='json')
        assert response.status_code == 201


# ===========================================================================
# Faculty Retrieve & Update — Role-based field mutation
# ===========================================================================

@pytest.mark.django_db
class TestFacultyRoleBasedFieldMutation:

    def test_faculty_cannot_change_own_designation(self, faculty_client, faculty_instance):
        pk = faculty_instance.employee_id.person_id
        original = faculty_instance.designation
        faculty_client.patch(
            reverse('Admin:faculty-detail', kwargs={'employee_id': pk}),
            {"designation": "Professor"},
            format='json'
        )
        faculty_instance.refresh_from_db()
        assert faculty_instance.designation == original

    def test_faculty_cannot_change_own_department(self, faculty_client, faculty_instance):
        pk = faculty_instance.employee_id.person_id
        original_dept = faculty_instance.department_id_id
        faculty_client.patch(
            reverse('Admin:faculty-detail', kwargs={'employee_id': pk}),
            {"department_id": "EE"},
            format='json'
        )
        faculty_instance.refresh_from_db()
        assert faculty_instance.department_id_id == original_dept

    def test_faculty_cannot_change_protected_person_fields(self, faculty_client, faculty_instance):
        pk = faculty_instance.employee_id.person_id
        original_name = faculty_instance.employee_id.first_name
        faculty_client.patch(
            reverse('Admin:faculty-detail', kwargs={'employee_id': pk}),
            {"person": {"first_name": "HackedName"}},
            format='json'
        )
        faculty_instance.employee_id.refresh_from_db()
        assert faculty_instance.employee_id.first_name == original_name

    def test_admin_can_change_faculty_designation(self, admin_client, faculty_instance):
        pk = faculty_instance.employee_id.person_id
        admin_client.patch(
            reverse('Admin:faculty-detail', kwargs={'employee_id': pk}),
            {"designation": "Senior Lecturer"},
            format='json'
        )
        faculty_instance.refresh_from_db()
        assert faculty_instance.designation == 'Senior Lecturer'


# ===========================================================================
# Student List & Create
# NOTE: students/ has no URL name, so we use the direct path
# ===========================================================================

@pytest.mark.django_db
class TestStudentListCreate:

    def test_unauthenticated_returns_401(self, anon_client):
        response = anon_client.get(f'{ADMIN}/students/')
        assert response.status_code == 401

    def test_student_user_cannot_access_list(self, student_client):
        response = student_client.get(f'{ADMIN}/students/')
        assert response.status_code == 403

    def test_faculty_user_cannot_access_list(self, faculty_client):
        response = faculty_client.get(f'{ADMIN}/students/')
        assert response.status_code == 403

    def test_admin_can_list_students(self, admin_client, student_instance):
        response = admin_client.get(f'{ADMIN}/students/')
        assert response.status_code == 200
        assert response.data['count'] >= 1


# ===========================================================================
# Course — validation edge cases
# NOTE: courses/ has no URL name
# ===========================================================================

@pytest.mark.django_db
class TestCourseValidation:

    def test_credit_hours_cannot_be_negative(self, admin_client):
        response = admin_client.post(f'{ADMIN}/courses/', {
            "course_code": "CS-999",
            "course_name": "Bad Course",
            "credit_hours": -1,
            "lab": False,
        }, format='json')
        assert response.status_code == 400

    def test_credit_hours_cannot_exceed_5(self, admin_client):
        response = admin_client.post(f'{ADMIN}/courses/', {
            "course_code": "CS-998",
            "course_name": "Too Many Credits",
            "credit_hours": 6,
            "lab": False,
        }, format='json')
        assert response.status_code == 400

    def test_lab_course_auto_increments_credit_hours(self, admin_client):
        """Creating a lab course should add 1 to credit_hours."""
        response = admin_client.post(f'{ADMIN}/courses/', {
            "course_code": "CS-997",
            "course_name": "Lab Course",
            "credit_hours": 3,
            "lab": True,
        }, format='json')
        assert response.status_code == 201
        from Models.models import Course
        course = Course.objects.get(course_code='CS-997')
        assert course.credit_hours == 4


# ===========================================================================
# Enrollment — permission based on status
# NOTE: enrollments/ has no URL name
# ===========================================================================

@pytest.mark.django_db
class TestEnrollmentPermissions:

    def test_admin_can_create_enrollment(self, admin_client, student_instance, course_allocation):
        course_allocation.status = 'Ongoing'
        course_allocation.save()
        response = admin_client.post(f'{ADMIN}/enrollments/', {
            "student_id": student_instance.pk,
            "allocation_id": course_allocation.allocation_id,
        }, format='json')
        assert response.status_code == 201

    def test_completed_enrollment_cannot_be_deleted(self, admin_client, enrollment):
        enrollment.result.course_gpa = 3.5
        enrollment.result.save()
        enrollment.status = 'Completed'
        enrollment.save()
        url = reverse('Admin:enrollment-detail', kwargs={'enrollment_id': enrollment.enrollment_id})
        response = admin_client.delete(url)
        assert response.status_code == 403