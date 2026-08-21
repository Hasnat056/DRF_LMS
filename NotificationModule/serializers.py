from rest_framework import serializers

from Models.models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    target_type = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = [
            'id',
            'verb',
            'message',
            'level',
            'target_type',
            'object_id',
            'read_at',
            'created_at',
        ]
        read_only_fields = fields

    def get_target_type(self, obj):
        return obj.content_type.model if obj.content_type else None


class NotificationMarkReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ['id', 'read_at']
        read_only_fields = fields
