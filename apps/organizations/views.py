from django.db import transaction
from django.db.models import Count, OuterRef, Subquery
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import filters, mixins, permissions, status, viewsets
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsOrgAdmin
from apps.core.tenancy import ORG_HEADER_PARAMETER, OrgScopedMixin
from apps.notifications.tasks import send_invitation_email

from .filters import InvitationFilter
from .models import Invitation, Membership, Organization, Role
from .permissions import MembershipPermission, OrganizationPermission
from .serializers import (
    InvitationAcceptedSerializer,
    InvitationAcceptSerializer,
    InvitationCreatedSerializer,
    InvitationCreateSerializer,
    InvitationSerializer,
    MembershipRoleSerializer,
    MembershipSerializer,
    OrganizationSerializer,
)


class OrganizationViewSet(viewsets.ModelViewSet):
    """Orgs the current user belongs to.

    Not scoped by X-Org-ID: this is where the user lists and picks their orgs.
    Orgs they don't belong to are simply absent from the queryset (-> 404).
    """

    serializer_class = OrganizationSerializer
    permission_classes = [permissions.IsAuthenticated, OrganizationPermission]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "slug"]
    ordering_fields = ["name", "created_at"]
    ordering = ["name"]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Organization.objects.none()
        my_role = Membership.objects.filter(
            organization=OuterRef("pk"), user=self.request.user
        ).values("role")[:1]
        return (
            Organization.objects.annotate(
                my_role=Subquery(my_role),
                member_count=Count("memberships", distinct=True),
            )
            .filter(my_role__isnull=False)
            .select_related("owner")
        )

    def perform_create(self, serializer):
        org = serializer.save(owner=self.request.user)
        Membership.objects.create(user=self.request.user, organization=org, role=Role.OWNER)
        # Fill in the annotated fields so the response matches the read shape.
        org.my_role = Role.OWNER
        org.member_count = 1


@extend_schema(parameters=[ORG_HEADER_PARAMETER])
class MembershipViewSet(
    OrgScopedMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Members of the current org (X-Org-ID).

    No create: people join through invitations. See MembershipPermission
    for who may change roles or remove members.
    """

    queryset = Membership.objects.select_related("user")
    serializer_class = MembershipSerializer
    permission_classes = [MembershipPermission]
    filterset_fields = ["role"]
    search_fields = ["user__email", "user__first_name", "user__last_name"]
    ordering_fields = ["created_at", "role", "user__email"]
    ordering = ["created_at"]
    http_method_names = ["get", "patch", "delete", "head", "options"]

    def get_serializer_class(self):
        if self.action == "partial_update":
            return MembershipRoleSerializer
        return MembershipSerializer

    @extend_schema(request=MembershipRoleSerializer, responses=MembershipSerializer)
    def partial_update(self, request, *args, **kwargs):
        membership = self.get_object()
        serializer = MembershipRoleSerializer(membership, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(MembershipSerializer(membership).data)


@extend_schema(parameters=[ORG_HEADER_PARAMETER])
class InvitationViewSet(
    OrgScopedMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Invitations for the current org (X-Org-ID). Owner/admin only."""

    queryset = Invitation.objects.select_related("invited_by")
    serializer_class = InvitationSerializer
    permission_classes = [IsOrgAdmin]
    filterset_class = InvitationFilter
    search_fields = ["email"]
    ordering_fields = ["created_at", "expires_at", "email"]
    ordering = ["-created_at"]

    @extend_schema(
        request=InvitationCreateSerializer,
        responses={201: InvitationCreatedSerializer, 200: InvitationCreatedSerializer},
        description=(
            "Invite an email to the current org. If an open (not accepted) invite "
            "already exists for that email, it is refreshed (new token and expiry, "
            "updated role) and 200 is returned instead of 201."
        ),
    )
    def create(self, request, *args, **kwargs):
        serializer = InvitationCreateSerializer(
            data=request.data, context={"organization": self.organization}
        )
        serializer.is_valid(raise_exception=True)
        email, role = serializer.validated_data["email"], serializer.validated_data["role"]

        invitation = Invitation.objects.filter(
            organization=self.organization, email=email, accepted_at__isnull=True
        ).first()
        if invitation:
            invitation.refresh(role=role, invited_by=request.user)
            response_status = status.HTTP_200_OK
        else:
            invitation = Invitation.objects.create(
                organization=self.organization,
                email=email,
                role=role,
                invited_by=request.user,
            )
            response_status = status.HTTP_201_CREATED
        transaction.on_commit(lambda: send_invitation_email.delay(invitation.pk))
        return Response(InvitationCreatedSerializer(invitation).data, status=response_status)

    def perform_destroy(self, instance):
        if instance.accepted_at is not None:
            raise ValidationError("Accepted invitations can't be revoked.")
        instance.delete()


class AcceptInvitationView(APIView):
    """Join an org with an invitation token. No X-Org-ID: you aren't a member yet."""

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        request=InvitationAcceptSerializer,
        responses={201: InvitationAcceptedSerializer},
        tags=["invitations"],
    )
    def post(self, request):
        serializer = InvitationAcceptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        invitation = get_object_or_404(
            Invitation.objects.select_for_update().select_related("organization"),
            token=serializer.validated_data["token"],
        )
        if invitation.accepted_at is not None:
            raise ValidationError({"token": ["This invitation has already been used."]})
        if invitation.is_expired:
            raise ValidationError({"token": ["This invitation has expired."]})
        if invitation.email != request.user.email.lower():
            raise PermissionDenied("This invitation was sent to a different email address.")
        if invitation.organization.memberships.filter(user=request.user).exists():
            raise ValidationError({"token": ["You're already a member of this organization."]})

        Membership.objects.create(
            user=request.user, organization=invitation.organization, role=invitation.role
        )
        invitation.accepted_at = timezone.now()
        invitation.save(update_fields=["accepted_at", "updated_at"])

        data = InvitationAcceptedSerializer(
            {"organization": invitation.organization, "role": invitation.role}
        ).data
        return Response(data, status=status.HTTP_201_CREATED)
