from rest_framework import serializers

from apps.organizations.serializers import OrganizationSummarySerializer

from .models import Notification, NotificationType


class NotificationSerializer(serializers.ModelSerializer):
    organization = OrganizationSummarySerializer(read_only=True)
    type = serializers.ChoiceField(choices=NotificationType.choices, read_only=True)
    payload = serializers.DictField(
        read_only=True,
        help_text=(
            "task_id, task_title, project_id, project_name, actor {id, email}; "
            "plus comment_id/excerpt (comment_added) or due_date (task_due_soon)."
        ),
    )

    class Meta:
        model = Notification
        fields = ["id", "type", "organization", "payload", "is_read", "read_at", "created_at"]
        read_only_fields = fields
