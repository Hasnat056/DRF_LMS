"""
test_views.py
--------------
HTTP integration tests for NotificationModule.

Covers:
  - NotificationListAPIView      : scoped to request.user, ?unread= filter
  - NotificationUnreadCountAPIView
  - NotificationMarkReadAPIView  : marks read, can't touch another user's notification
  - NotificationMarkAllReadAPIView
"""

import pytest
from django.utils import timezone

from Models.models import Notification

NOTIFICATIONS = '/api/notifications'


@pytest.fixture
def admin_notification(db, admin_user):
    return Notification.objects.create(
        recipient=admin_user,
        verb='change_request_confirmed',
        message='Your request has been confirmed.',
    )


@pytest.fixture
def faculty_notification(db, faculty_user):
    return Notification.objects.create(
        recipient=faculty_user,
        verb='hod_nomination',
        message='You have been nominated as HOD.',
        level='action_required',
    )


# ===========================================================================
# NotificationListAPIView
# ===========================================================================

class TestNotificationListAPIView:
    def test_requires_auth(self, anon_client):
        response = anon_client.get(f'{NOTIFICATIONS}/')
        assert response.status_code == 401

    def test_lists_only_own_notifications(self, admin_client, admin_notification, faculty_notification):
        response = admin_client.get(f'{NOTIFICATIONS}/')
        assert response.status_code == 200
        ids = [item['id'] for item in response.data['results']]
        assert admin_notification.pk in ids
        assert faculty_notification.pk not in ids

    def test_unread_filter(self, admin_client, admin_user):
        Notification.objects.create(recipient=admin_user, verb='x', message='read', read_at=timezone.now())
        Notification.objects.create(recipient=admin_user, verb='y', message='unread')

        response = admin_client.get(f'{NOTIFICATIONS}/', {'unread': 'true'})
        assert response.status_code == 200
        results = response.data['results']
        assert all(item['read_at'] is None for item in results)
        assert len(results) == 1


# ===========================================================================
# NotificationUnreadCountAPIView
# ===========================================================================

class TestNotificationUnreadCountAPIView:
    def test_counts_only_unread_for_current_user(self, admin_client, admin_user, faculty_notification):
        Notification.objects.create(recipient=admin_user, verb='a', message='one')
        Notification.objects.create(recipient=admin_user, verb='b', message='two')

        response = admin_client.get(f'{NOTIFICATIONS}/unread-count/')
        assert response.status_code == 200
        assert response.data['unread_count'] == 2


# ===========================================================================
# NotificationMarkReadAPIView
# ===========================================================================

class TestNotificationMarkReadAPIView:
    def test_marks_own_notification_read(self, admin_client, admin_notification):
        assert admin_notification.read_at is None
        response = admin_client.patch(f'{NOTIFICATIONS}/{admin_notification.pk}/read/')
        assert response.status_code == 200
        admin_notification.refresh_from_db()
        assert admin_notification.read_at is not None

    def test_cannot_mark_another_users_notification(self, faculty_client, admin_notification):
        response = faculty_client.patch(f'{NOTIFICATIONS}/{admin_notification.pk}/read/')
        assert response.status_code == 404
        admin_notification.refresh_from_db()
        assert admin_notification.read_at is None

    def test_idempotent_on_already_read(self, admin_client, admin_notification):
        first = admin_client.patch(f'{NOTIFICATIONS}/{admin_notification.pk}/read/')
        first_read_at = first.data['read_at']
        second = admin_client.patch(f'{NOTIFICATIONS}/{admin_notification.pk}/read/')
        assert second.data['read_at'] == first_read_at


# ===========================================================================
# NotificationMarkAllReadAPIView
# ===========================================================================

class TestNotificationMarkAllReadAPIView:
    def test_marks_all_own_unread_notifications(self, admin_client, admin_user, faculty_notification):
        Notification.objects.create(recipient=admin_user, verb='a', message='one')
        Notification.objects.create(recipient=admin_user, verb='b', message='two')

        response = admin_client.patch(f'{NOTIFICATIONS}/mark-all-read/')
        assert response.status_code == 200
        assert response.data['marked_read'] == 2
        assert Notification.objects.filter(recipient=admin_user, read_at__isnull=True).count() == 0

        faculty_notification.refresh_from_db()
        assert faculty_notification.read_at is None
