"""Multi-tenancy: resolve the current organization from the X-Org-ID header.

Usage (put the mixin *before* the DRF base class):

    @extend_schema(parameters=[ORG_HEADER_PARAMETER])
    class ProjectViewSet(OrgScopedMixin, viewsets.ModelViewSet):
        queryset = Project.objects.all()          # filtered to the org automatically
        org_lookup = "organization"               # path from the model to its org

For models linked to the org indirectly (e.g. Task -> Project -> Organization),
set `org_lookup = "project__organization"` and override `perform_create`.
"""
from functools import cached_property

from drf_spectacular.utils import OpenApiParameter
from rest_framework.exceptions import NotAuthenticated, NotFound, ValidationError

from apps.organizations.models import Membership

ORG_HEADER = "X-Org-ID"

# Add to @extend_schema(parameters=[...]) so the header shows up in the API docs.
ORG_HEADER_PARAMETER = OpenApiParameter(
    name=ORG_HEADER,
    type=int,
    location=OpenApiParameter.HEADER,
    required=True,
    description="ID of the organization this request acts on.",
)


def resolve_membership(request):
    """Return the user's Membership in the org named by the X-Org-ID header.

    Raises 401 if not logged in, 400 if the header is missing or malformed,
    and 404 if the org doesn't exist or the user isn't a member (so outsiders
    can't tell which orgs exist).
    """
    if not request.user or not request.user.is_authenticated:
        raise NotAuthenticated()

    raw = request.headers.get(ORG_HEADER)
    if not raw:
        raise ValidationError({ORG_HEADER: "This header is required."})
    try:
        org_id = int(raw)
    except ValueError:
        raise ValidationError({ORG_HEADER: "Must be an integer organization ID."})

    try:
        return Membership.objects.select_related("organization").get(
            user=request.user, organization_id=org_id
        )
    except Membership.DoesNotExist:
        raise NotFound("Organization not found.")


class OrgScopedMixin:
    """Scope a DRF view to the current organization.

    - `self.membership` / `self.organization` (also `request.membership` /
      `request.organization`) are resolved once per request.
    - `get_queryset()` is filtered to the current org.
    - `perform_create()` sets `organization` server-side.
    """

    org_lookup = "organization"

    @cached_property
    def membership(self):
        membership = resolve_membership(self.request)
        self.request.membership = membership
        self.request.organization = membership.organization
        return membership

    @property
    def organization(self):
        return self.membership.organization

    def initial(self, request, *args, **kwargs):
        # DRF runs authentication and permission checks here.
        super().initial(request, *args, **kwargs)
        # Then always enforce membership, even if no permission class asked for it.
        self.membership

    def get_queryset(self):
        queryset = super().get_queryset()
        # drf-spectacular builds views without a real request while generating docs.
        if getattr(self, "swagger_fake_view", False):
            return queryset.none()
        return queryset.filter(**{self.org_lookup: self.organization})

    def perform_create(self, serializer):
        serializer.save(organization=self.organization)
