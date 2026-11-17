from django.db.models import Count, OuterRef, Subquery
from drf_spectacular.utils import extend_schema
from rest_framework import filters, mixins, permissions, viewsets
from rest_framework.response import Response

from apps.core.tenancy import ORG_HEADER_PARAMETER, OrgScopedMixin

from .models import Membership, Organization, Role
from .permissions import MembershipPermission, OrganizationPermission
from .serializers import MembershipRoleSerializer, MembershipSerializer, OrganizationSerializer


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
