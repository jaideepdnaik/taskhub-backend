import pytest
from rest_framework import generics, serializers
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.accounts.tests.factories import UserFactory
from apps.core.tenancy import ORG_HEADER, OrgScopedMixin
from apps.organizations.models import Membership, Role
from apps.organizations.tests.factories import MembershipFactory, OrganizationFactory

HEADER = f"HTTP_{ORG_HEADER.upper().replace('-', '_')}"  # X-Org-ID -> HTTP_X_ORG_ID


class MembershipSerializer(serializers.ModelSerializer):
    class Meta:
        model = Membership
        fields = ["id", "user", "role"]


class MembershipListView(OrgScopedMixin, generics.ListAPIView):
    """Throwaway view: lists memberships, which have an `organization` field."""

    queryset = Membership.objects.all()
    serializer_class = MembershipSerializer
    pagination_class = None


@pytest.fixture
def call_view():
    factory = APIRequestFactory()

    def _call(user=None, org_id=None):
        extra = {HEADER: str(org_id)} if org_id is not None else {}
        request = factory.get("/fake/", **extra)
        if user:
            force_authenticate(request, user=user)
        return MembershipListView.as_view()(request)

    return _call


@pytest.mark.django_db
class TestOrgScopedMixin:
    def test_member_sees_only_their_org_rows(self, call_view):
        org_a = OrganizationFactory()
        org_b = OrganizationFactory()
        MembershipFactory(organization=org_a)
        MembershipFactory(organization=org_b)

        response = call_view(user=org_a.owner, org_id=org_a.id)

        assert response.status_code == 200
        ids = {row["id"] for row in response.data}
        assert ids == set(org_a.memberships.values_list("id", flat=True))
        assert len(ids) == 2  # owner + one member, nothing from org B

    def test_non_member_gets_404(self, call_view):
        org_a = OrganizationFactory()
        org_b = OrganizationFactory()

        response = call_view(user=org_b.owner, org_id=org_a.id)

        assert response.status_code == 404

    def test_nonexistent_org_gets_404(self, call_view):
        user = UserFactory()
        assert call_view(user=user, org_id=999999).status_code == 404

    def test_missing_header_gets_400(self, call_view):
        org = OrganizationFactory()
        response = call_view(user=org.owner)
        assert response.status_code == 400
        assert ORG_HEADER in response.data

    def test_malformed_header_gets_400(self, call_view):
        org = OrganizationFactory()
        assert call_view(user=org.owner, org_id="abc").status_code == 400

    def test_anonymous_gets_401(self, call_view):
        org = OrganizationFactory()
        assert call_view(org_id=org.id).status_code == 401

    def test_membership_attached_to_request(self):
        org = OrganizationFactory()
        viewer = MembershipFactory(organization=org, role=Role.VIEWER).user
        request = APIRequestFactory().get("/fake/", **{HEADER: str(org.id)})
        force_authenticate(request, user=viewer)

        view = MembershipListView()
        view.setup(request)
        drf_request = view.initialize_request(request)
        view.request = drf_request
        view.initial(drf_request)

        assert view.organization == org
        assert drf_request.organization == org
        assert drf_request.membership.role == Role.VIEWER
