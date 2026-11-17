import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.tests.factories import UserFactory
from apps.organizations.models import Membership, Organization, Role
from apps.organizations.tests.factories import MembershipFactory, OrganizationFactory

LIST_URL = "/api/organizations/"


def detail_url(org):
    return f"/api/organizations/{org.id}/"


@pytest.fixture
def client_for(api_client):
    """Return an API client logged in as the given user."""

    def _login(user):
        api_client.force_authenticate(user=user)
        return api_client

    return _login


def user_with_role(org, role):
    if role == Role.OWNER:
        return org.owner
    return MembershipFactory(organization=org, role=role).user


@pytest.mark.django_db
class TestCreate:
    def test_creator_becomes_owner(self, client_for):
        user = UserFactory()

        response = client_for(user).post(LIST_URL, {"name": "Acme Corp"})

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Acme Corp"
        assert data["slug"] == "acme-corp"
        assert data["owner"]["email"] == user.email
        assert data["my_role"] == Role.OWNER
        assert data["member_count"] == 1
        membership = Membership.objects.get(organization_id=data["id"])
        assert (membership.user, membership.role) == (user, Role.OWNER)

    def test_owner_and_slug_cannot_be_set_from_body(self, client_for):
        user, other = UserFactory(), UserFactory()

        response = client_for(user).post(
            LIST_URL, {"name": "Acme", "owner": other.id, "slug": "hacked"}
        )

        assert response.status_code == 201
        org = Organization.objects.get(id=response.json()["id"])
        assert org.owner == user
        assert org.slug == "acme"

    def test_name_is_required(self, client_for):
        response = client_for(UserFactory()).post(LIST_URL, {})
        assert response.status_code == 400
        assert "name" in response.json()

    def test_anonymous_gets_401(self, api_client):
        assert api_client.post(LIST_URL, {"name": "Acme"}).status_code == 401


@pytest.mark.django_db
class TestListAndRetrieve:
    def test_lists_only_my_orgs_with_my_role(self, client_for):
        mine = OrganizationFactory(name="Mine")
        joined = OrganizationFactory(name="Joined")
        OrganizationFactory(name="Not mine")
        MembershipFactory(organization=joined, user=mine.owner, role=Role.VIEWER)

        response = client_for(mine.owner).get(LIST_URL)

        assert response.status_code == 200
        results = {row["name"]: row for row in response.json()["results"]}
        assert set(results) == {"Mine", "Joined"}
        assert results["Mine"]["my_role"] == Role.OWNER
        assert results["Joined"]["my_role"] == Role.VIEWER
        assert results["Joined"]["member_count"] == 2

    def test_list_query_count_does_not_grow_with_orgs(self, client_for):
        user = UserFactory()
        for _ in range(5):
            MembershipFactory(user=user)
        client = client_for(user)

        with CaptureQueriesContext(connection) as ctx:
            response = client.get(LIST_URL)

        # Ignore SAVEPOINTs from ATOMIC_REQUESTS; count real reads only.
        selects = [q for q in ctx.captured_queries if q["sql"].startswith("SELECT")]
        assert len(selects) == 2  # page count + one query for all rows
        assert response.json()["count"] == 5

    def test_member_can_retrieve(self, client_for):
        org = OrganizationFactory()
        user = user_with_role(org, Role.VIEWER)

        response = client_for(user).get(detail_url(org))

        assert response.status_code == 200
        assert response.json()["my_role"] == Role.VIEWER

    def test_search(self, client_for):
        user = UserFactory()
        MembershipFactory(user=user, organization=OrganizationFactory(name="Acme"))
        MembershipFactory(user=user, organization=OrganizationFactory(name="Globex"))

        response = client_for(user).get(LIST_URL, {"search": "glob"})

        assert [row["name"] for row in response.json()["results"]] == ["Globex"]


@pytest.mark.django_db
class TestUpdate:
    @pytest.mark.parametrize("role", [Role.OWNER, Role.ADMIN])
    def test_admins_can_rename(self, client_for, role):
        org = OrganizationFactory(name="Old")
        response = client_for(user_with_role(org, role)).patch(
            detail_url(org), {"name": "New"}
        )
        assert response.status_code == 200
        org.refresh_from_db()
        assert org.name == "New"

    @pytest.mark.parametrize("role", [Role.MEMBER, Role.VIEWER])
    def test_non_admins_get_403(self, client_for, role):
        org = OrganizationFactory(name="Old")
        response = client_for(user_with_role(org, role)).patch(
            detail_url(org), {"name": "New"}
        )
        assert response.status_code == 403
        org.refresh_from_db()
        assert org.name == "Old"

    def test_put_is_not_allowed(self, client_for):
        org = OrganizationFactory()
        response = client_for(org.owner).put(detail_url(org), {"name": "New"})
        assert response.status_code == 405


@pytest.mark.django_db
class TestDelete:
    def test_owner_can_delete(self, client_for):
        org = OrganizationFactory()
        response = client_for(org.owner).delete(detail_url(org))
        assert response.status_code == 204
        assert not Organization.objects.filter(id=org.id).exists()
        assert not Membership.objects.filter(organization_id=org.id).exists()

    @pytest.mark.parametrize("role", [Role.ADMIN, Role.MEMBER, Role.VIEWER])
    def test_others_get_403(self, client_for, role):
        org = OrganizationFactory()
        response = client_for(user_with_role(org, role)).delete(detail_url(org))
        assert response.status_code == 403
        assert Organization.objects.filter(id=org.id).exists()


@pytest.mark.django_db
class TestTenantIsolation:
    """A user from org B can't read, update or delete org A."""

    @pytest.fixture
    def setup(self, client_for):
        org_a = OrganizationFactory(name="A")
        org_b = OrganizationFactory(name="B")
        return org_a, client_for(org_b.owner)

    def test_cannot_list(self, setup):
        org_a, client = setup
        names = [row["name"] for row in client.get(LIST_URL).json()["results"]]
        assert "A" not in names

    def test_cannot_retrieve(self, setup):
        org_a, client = setup
        assert client.get(detail_url(org_a)).status_code == 404

    def test_cannot_update(self, setup):
        org_a, client = setup
        assert client.patch(detail_url(org_a), {"name": "Pwned"}).status_code == 404
        org_a.refresh_from_db()
        assert org_a.name == "A"

    def test_cannot_delete(self, setup):
        org_a, client = setup
        assert client.delete(detail_url(org_a)).status_code == 404
        assert Organization.objects.filter(id=org_a.id).exists()
