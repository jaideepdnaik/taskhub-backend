from django.conf import settings
from rest_framework import serializers

from apps.accounts.serializers import UserSummarySerializer

from .models import Invitation, InvitationStatus, Membership, Organization, Role

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


class OrganizationSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ["id", "name", "slug"]
        read_only_fields = fields


class InvitationSerializer(serializers.ModelSerializer):
    """Read shape for an invitation. Never exposes the token."""

    invited_by = UserSummarySerializer(read_only=True)
    status = serializers.ChoiceField(choices=InvitationStatus.choices, read_only=True)

    class Meta:
        model = Invitation
        fields = [
            "id",
            "email",
            "role",
            "status",
            "invited_by",
            "expires_at",
            "accepted_at",
            "created_at",
        ]
        read_only_fields = fields


class InvitationCreatedSerializer(InvitationSerializer):
    """Response to creating/refreshing an invite: adds the one-time accept link.

    Until invitation emails exist (roadmap step 6), the admin shares this link.
    """

    accept_url = serializers.SerializerMethodField()

    class Meta(InvitationSerializer.Meta):
        fields = InvitationSerializer.Meta.fields + ["accept_url"]
        read_only_fields = fields

    def get_accept_url(self, obj) -> str:
        return f"{settings.FRONTEND_URL}/invitations/accept?token={obj.token}"


class InvitationCreateSerializer(serializers.Serializer):
    """Write shape for inviting someone. Expects `organization` in context."""

    email = serializers.EmailField()
    role = serializers.ChoiceField(choices=ASSIGNABLE_ROLES, default=Role.MEMBER)

    def validate_email(self, value):
        email = value.strip().lower()
        org = self.context["organization"]
        if org.memberships.filter(user__email__iexact=email).exists():
            raise serializers.ValidationError("This person is already a member.")
        return email


class InvitationAcceptSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=64)


class InvitationAcceptedSerializer(serializers.Serializer):
    """Response to accepting an invite: the org joined and the role given."""

    organization = OrganizationSummarySerializer()
    role = serializers.ChoiceField(choices=Role.choices)
