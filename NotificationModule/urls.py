from django.urls import path

from .views import (
    NotificationListAPIView,
    NotificationUnreadCountAPIView,
    NotificationMarkReadAPIView,
    NotificationMarkAllReadAPIView,
)

app_name = 'Notifications'
urlpatterns = [
    path('', NotificationListAPIView.as_view(), name='notification-list'),
    path('unread-count/', NotificationUnreadCountAPIView.as_view(), name='notification-unread-count'),
    path('mark-all-read/', NotificationMarkAllReadAPIView.as_view(), name='notification-mark-all-read'),
    path('<int:pk>/read/', NotificationMarkReadAPIView.as_view(), name='notification-mark-read'),
]
