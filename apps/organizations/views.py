from django.db.models import Count, OuterRef, Subquery
from rest_framework import filters, permissions, viewsets

from .models import Membership, Organization, Role
from .permissions import OrganizationPermission
from .serializers import OrganizationSerializer


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
