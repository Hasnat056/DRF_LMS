"""
test_permissions.py
-------------------
Permission gate tests for every StudentModule endpoint.
No heavy DB setup — verifies role enforcement at the HTTP layer.
"""
import pytest
from rest_framework.test import APIClient

STUDENT = '/api/student'


@pytest.mark.django_db
class TestUnauthenticated:
    def test_dashboard_401(self, anon_client):
        r = anon_client.get(f'{STUDENT}/dashboard/')
        assert r.status_code == 401

    def test_profile_401(self, anon_client):
        r = anon_client.get(f'{STUDENT}/profile/')
        assert r.status_code == 401

    def test_enrollments_401(self, anon_client):
        r = anon_client.get(f'{STUDENT}/enrollments/')
        assert r.status_code == 401

    def test_attendance_401(self, anon_client):
        r = anon_client.get(f'{STUDENT}/attendance/')
        assert r.status_code == 401

    def test_compilers_401(self, anon_client):
        r = anon_client.get(f'{STUDENT}/compilers/')
        assert r.status_code == 401


@pytest.mark.django_db
class TestFacultyCannotAccessStudentEndpoints:
    def test_dashboard_403(self, faculty_client):
        r = faculty_client.get(f'{STUDENT}/dashboard/')
        assert r.status_code == 403

    def test_profile_403(self, faculty_client):
        r = faculty_client.get(f'{STUDENT}/profile/')
        assert r.status_code == 403

    def test_enrollments_403(self, faculty_client):
        r = faculty_client.get(f'{STUDENT}/enrollments/')
        assert r.status_code == 403


@pytest.mark.django_db
class TestAdminCannotAccessStudentEndpoints:
    def test_dashboard_403(self, admin_client):
        r = admin_client.get(f'{STUDENT}/dashboard/')
        assert r.status_code == 403

    def test_profile_403(self, admin_client):
        r = admin_client.get(f'{STUDENT}/profile/')
        assert r.status_code == 403

    def test_enrollments_403(self, admin_client):
        r = admin_client.get(f'{STUDENT}/enrollments/')
        assert r.status_code == 403


@pytest.mark.django_db
class TestStudentPermissions:
    def test_dashboard_200(self, student_client):
        r = student_client.get(f'{STUDENT}/dashboard/')
        assert r.status_code == 200

    def test_profile_get_200(self, student_client):
        r = student_client.get(f'{STUDENT}/profile/')
        assert r.status_code == 200

    def test_enrollments_200(self, student_client):
        r = student_client.get(f'{STUDENT}/enrollments/')
        assert r.status_code == 200

    def test_attendance_200(self, student_client):
        r = student_client.get(f'{STUDENT}/attendance/')
        assert r.status_code == 200

    def test_compilers_get_200(self, student_client):
        r = student_client.get(f'{STUDENT}/compilers/')
        assert r.status_code == 200


@pytest.mark.django_db
class TestStudentWriteRestrictions:
    """StudentPermissions only allows GET/PUT/PATCH — not DELETE or POST on profile."""

    def test_profile_put_allowed(self, student_client, student_instance):
        r = student_client.put(f'{STUDENT}/profile/', {}, format='json')
        # 400 (bad data) or 200 — but NOT 403/405
        assert r.status_code in (200, 400)

    def test_profile_patch_allowed(self, student_client):
        r = student_client.patch(f'{STUDENT}/profile/', {}, format='json')
        assert r.status_code in (200, 400)


@pytest.mark.django_db
class TestEnrollmentCreatePermission:
    """StudentEnrollmentCreatePermission requires the semester allocation cache to be set."""

    def test_create_403_when_cache_empty(self, student_client):
        """No cache key → permission returns False → 403."""
        r = student_client.get(f'{STUDENT}/enrollments/create/')
        assert r.status_code == 403

    def test_create_200_when_cache_primed(self, student_client, primed_enrollment_cache):
        """Cache key present → permission passes → 200."""
        r = student_client.get(f'{STUDENT}/enrollments/create/')
        assert r.status_code == 200


@pytest.mark.django_db
class TestReviewPermissions:
    """ReviewPermission: Students can read/create/update, not delete.
    Admin/Faculty can only read."""

    def test_student_can_list_reviews(self, student_client, student_instance):
        sid = student_instance.student_id.person_id
        r = student_client.get(f'{STUDENT}/{sid}/enrollments/reviews/')
        assert r.status_code == 200

    def test_faculty_can_list_reviews(self, faculty_client, student_instance):
        sid = student_instance.student_id.person_id
        r = faculty_client.get(f'{STUDENT}/{sid}/enrollments/reviews/')
        assert r.status_code == 200

    def test_anon_cannot_list_reviews(self, anon_client, student_instance):
        sid = student_instance.student_id.person_id
        r = anon_client.get(f'{STUDENT}/{sid}/enrollments/reviews/')
        assert r.status_code == 401
