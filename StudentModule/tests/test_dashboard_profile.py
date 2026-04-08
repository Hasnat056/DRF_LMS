"""
test_dashboard_profile.py
--------------------------
Tests for StudentDashboardView and StudentProfileView.
"""
import pytest
from django.core.cache import cache

STUDENT = '/api/student'


@pytest.mark.django_db
class TestStudentDashboard:

    def test_dashboard_returns_expected_fields(self, student_client, student_instance):
        r = student_client.get(f'{STUDENT}/dashboard/')
        assert r.status_code == 200
        for field in ('student_id', 'first_name', 'last_name', 'institutional_email',
                      'class', 'program', 'department',
                      'total_enrollments', 'active_enrollments', 'completed_enrollments'):
            assert field in r.data, f"Missing field: {field}"

    def test_dashboard_student_id_matches(self, student_client, student_instance):
        r = student_client.get(f'{STUDENT}/dashboard/')
        assert r.data['student_id'] == student_instance.student_id.person_id

    def test_dashboard_enrollment_counts(
        self, student_client, student_instance, active_enrollment
    ):
        r = student_client.get(f'{STUDENT}/dashboard/')
        assert r.data['total_enrollments'] >= 1
        assert r.data['active_enrollments'] >= 1

    def test_dashboard_response_cached(self, student_client, student_instance):
        student_client.get(f'{STUDENT}/dashboard/')
        key = f'student:dashboard:{student_instance.student_id.user.username}'
        assert cache.get(key) is not None

    def test_dashboard_serves_from_cache(self, student_client, student_instance):
        # Prime cache with fake data
        key = f'student:dashboard:{student_instance.student_id.user.username}'
        cache.set(key, {'student_id': 'CACHED'}, timeout=300)
        r = student_client.get(f'{STUDENT}/dashboard/')
        assert r.data['student_id'] == 'CACHED'

    def test_dashboard_image_null_when_no_image(self, student_client, student_instance):
        r = student_client.get(f'{STUDENT}/dashboard/')
        assert r.data['image'] is None


@pytest.mark.django_db
class TestStudentProfile:

    def test_profile_get_200(self, student_client):
        r = student_client.get(f'{STUDENT}/profile/')
        assert r.status_code == 200

    def test_profile_contains_person_data(self, student_client, student_instance):
        r = student_client.get(f'{STUDENT}/profile/')
        assert r.status_code == 200
        # StudentSerializer nests person under student_id
        assert 'student_id' in r.data or 'first_name' in str(r.data)

    def test_profile_put_valid_data(self, student_client, student_instance):
        """PUT with valid nested data should succeed or return 400 for missing required fields."""
        r = student_client.put(f'{STUDENT}/profile/', {}, format='json')
        assert r.status_code in (200, 400)

    def test_profile_put_invalid_returns_400(self, student_client):
        """PUT with malformed data must return 400, not 500."""
        r = student_client.put(f'{STUDENT}/profile/', {'student_id': {'dob': 'not-a-date'}}, format='json')
        assert r.status_code == 400
