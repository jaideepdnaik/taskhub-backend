from django.contrib.auth import get_user_model
from rest_framework import serializers


class UserSummarySerializer(serializers.ModelSerializer):
    """Compact, read-only user shape for nesting (owner, assignee, author...)."""

    full_name = serializers.CharField(source="get_full_name", read_only=True)

    class Meta:
        model = get_user_model()
        fields = ["id", "email", "full_name"]
        read_only_fields = fields
