from rest_framework import permissions


class IsNotificationRecipient(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        return request.user.is_authenticated and obj.recipient_id == request.user.id
