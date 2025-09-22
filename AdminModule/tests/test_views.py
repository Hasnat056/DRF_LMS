import pytest
from rest_framework.test import APIClient
from django.urls import reverse
from django.contrib.auth.models import User


@pytest.mark.django_db
def test_dashboard_unauthenticated_access():
    """
    Ensure that unauthenticated users cannot access the admin dashboard.
    """
    client = APIClient()  # No authentication
    url = "/api/admin/dashboard/"  # Endpoint

    response = client.get(url)

    # Assert: Must return 401 Unauthorized
    assert response.status_code == 401
    assert response.data["detail"] == "Authentication credentials were not provided."


@pytest.mark.django_db
class TestAdminDashboardAuthentication:


    def test_dashboard_unauthenticated_access_forbidden(self):
        """Unauthenticated users must not access the dashboard."""
        client = APIClient()
        url = reverse('Admin:admin-dashboard')  # Make sure your URL name matches
        response = client.get(url)
        assert response.status_code == 401

    def test_dashboard_authenticated_non_admin_forbidden(self):
        """Authenticated but non-admin users must get 403."""
        from django.conf import settings
        print("Current Test DB:", settings.DATABASES['default']['NAME'])

        client = APIClient()
        token_url = reverse('token_obtain_pair')  # usually api/token
        token_response = client.post(token_url, {
            'username': 'bscs23f02@gmail.com',
            'password': 'mylms123'
        }, format='json')
        print(User.objects.all())
        print(token_response)
        assert token_response.status_code == 200
        access_token = token_response.data['access']

        # print("TOKEN RESPONSE:", token_response.status_code, token_response.data)
        # Use token to access dashboard
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')
        url = reverse('Admin:admin-dashboard')
        response = client.get(url)
        assert response.status_code == 403
        client.logout()

    def test_dashboard_authenticated_admin_jwt(self):
        """Admin user with JWT token should get 200 OK."""
        client = APIClient()
        token_url = reverse('token_obtain_pair')  # usually api/token
        token_response = client.post(token_url, {
            'username': 'rhays056@gmail.com',
            'password': 'admin12345678'
        }, format='json')
        assert token_response.status_code == 200
        access_token = token_response.data['access']

        #print("TOKEN RESPONSE:", token_response.status_code, token_response.data)
        # Use token to access dashboard
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')
        dashboard_url = reverse('Admin:admin-dashboard')
        response = client.get(dashboard_url)
        assert response.status_code == 200

    def test_dashboard_authenticated_admin_session(self):
        """Admin user authenticated via Django session should get 200 OK."""
        client = APIClient()
        url = reverse('Admin:admin-dashboard')
        logged_in = client.login(username='rhays056@gmail.com', password='admin12345678')
        assert logged_in  # sanity check

        response = client.get(url)
        assert response.status_code == 200
        client.logout()
