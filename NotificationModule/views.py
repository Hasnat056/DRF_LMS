import logging

from django.utils import timezone
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from Models.models import Notification
from .permissions import IsNotificationRecipient
from .serializers import NotificationSerializer, NotificationMarkReadSerializer

logger = logging.getLogger(__name__)


class NotificationListAPIView(generics.ListAPIView):
    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = Notification.objects.filter(recipient=self.request.user)
        unread = self.request.query_params.get('unread')
        if unread is not None and unread.lower() in ('1', 'true', 'yes'):
            queryset = queryset.filter(read_at__isnull=True)
        return queryset


class NotificationUnreadCountAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        count = Notification.objects.filter(recipient=request.user, read_at__isnull=True).count()
        return Response({'unread_count': count}, status=status.HTTP_200_OK)


class NotificationMarkReadAPIView(generics.UpdateAPIView):
    queryset = Notification.objects.all()
    serializer_class = NotificationMarkReadSerializer
    permission_classes = [IsAuthenticated, IsNotificationRecipient]
    http_method_names = ['patch']

    def get_queryset(self):
        return Notification.objects.filter(recipient=self.request.user)

    def patch(self, request, *args, **kwargs):
        notification = self.get_object()
        if notification.read_at is None:
            notification.read_at = timezone.now()
            notification.save(update_fields=['read_at'])
        return Response(self.get_serializer(notification).data, status=status.HTTP_200_OK)


class NotificationMarkAllReadAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, *args, **kwargs):
        updated = Notification.objects.filter(
            recipient=request.user, read_at__isnull=True
        ).update(read_at=timezone.now())
        logger.info('Marked %s notifications read for user=%s', updated, request.user.username)
        return Response({'marked_read': updated}, status=status.HTTP_200_OK)
