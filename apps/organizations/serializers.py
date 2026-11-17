from rest_framework import serializers

from apps.accounts.serializers import UserSummarySerializer

from .models import Membership, Organization, Role

# Roles that can be assigned through the API. Ownership is never handed out here.
ASSIGNABLE_ROLES = [(value, label) for value, label in Role.choices if value != Role.OWNER]


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


class MembershipSerializer(serializers.ModelSerializer):
    """Read shape: a member of the current org."""

    user = UserSummarySerializer(read_only=True)
    joined_at = serializers.DateTimeField(source="created_at", read_only=True)

    class Meta:
        model = Membership
        fields = ["id", "user", "role", "joined_at"]
        read_only_fields = fields


class MembershipRoleSerializer(serializers.ModelSerializer):
    """Write shape: change a member's role (never to owner)."""

    role = serializers.ChoiceField(choices=ASSIGNABLE_ROLES)

    class Meta:
        model = Membership
        fields = ["role"]
