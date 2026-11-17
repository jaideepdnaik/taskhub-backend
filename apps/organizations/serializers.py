from rest_framework import serializers

from apps.accounts.serializers import UserSummarySerializer

from .models import Organization, Role


class OrganizationSerializer(serializers.ModelSerializer):
    """Read and write shape for an organization.

    Only `name` is writable. `owner` and `slug` are set by the server, and
    `my_role` / `member_count` come from annotations in the view's queryset.
    """

    owner = UserSummarySerializer(read_only=True)
    my_role = serializers.ChoiceField(choices=Role.choices, read_only=True)
    member_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Organization
        fields = [
            "id",
            "name",
            "slug",
            "owner",
            "my_role",
            "member_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["slug", "created_at", "updated_at"]
