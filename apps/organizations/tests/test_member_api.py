import pytest

from apps.organizations.models import Membership, Role
from apps.organizations.tests.factories import MembershipFactory, OrganizationFactory

LIST_URL = "/api/members/"
OWNER, ADMIN, MEMBER, VIEWER = Role.OWNER, Role.ADMIN, Role.MEMBER, Role.VIEWER


def detail_url(membership):
    return f"/api/members/{membership.id}/"


@pytest.fixture
def org():
    return OrganizationFactory()


@pytest.fixture
def as_user(api_client):
    """Log in as `user` acting on `org` (sets the X-Org-ID header)."""

    def _as(user, org):
        api_client.force_authenticate(user=user)
        api_client.credentials(HTTP_X_ORG_ID=str(org.id))
        return api_client

    return _as


def member(org, role):
    """Return the Membership for a user with `role` in `org`."""
    if role == OWNER:
        return org.memberships.get(role=OWNER)
    return MembershipFactory(organization=org, role=role)


@pytest.mark.django_db
class TestList:
    @pytest.mark.parametrize("role", [OWNER, ADMIN, MEMBER, VIEWER])
    def test_every_role_can_list(self, as_user, org, role):
        me = member(org, role)
        other = member(org, VIEWER if role != VIEWER else MEMBER)

        response = as_user(me.user, org).get(LIST_URL)

        assert response.status_code == 200
        ids = {row["id"] for row in response.json()["results"]}
        assert {me.id, other.id} <= ids

    def test_shape_includes_user_summary(self, as_user, org):
        owner = member(org, OWNER)
        row = as_user(owner.user, org).get(detail_url(owner)).json()
        assert row["user"] == {
            "id": owner.user.id,
            "email": owner.user.email,
            "full_name": owner.user.get_full_name(),
        }
        assert row["role"] == OWNER
        assert "joined_at" in row

    def test_filter_by_role(self, as_user, org):
        member(org, ADMIN)
        member(org, VIEWER)
        response = as_user(member(org, OWNER).user, org).get(LIST_URL, {"role": VIEWER})
        assert [row["role"] for row in response.json()["results"]] == [VIEWER]

    def test_search_by_email(self, as_user, org):
        target = member(org, MEMBER)
        response = as_user(member(org, OWNER).user, org).get(
            LIST_URL, {"search": target.user.email}
        )
        assert [row["id"] for row in response.json()["results"]] == [target.id]

    def test_missing_header_gets_400(self, api_client, org):
        api_client.force_authenticate(user=org.owner)
        assert api_client.get(LIST_URL).status_code == 400

    def test_create_is_not_allowed(self, as_user, org):
        response = as_user(org.owner, org).post(LIST_URL, {"role": MEMBER})
        assert response.status_code == 405


@pytest.mark.django_db
class TestChangeRole:
    @pytest.mark.parametrize(
        "actor_role,target_role,new_role",
        [
            (OWNER, MEMBER, ADMIN),
            (OWNER, ADMIN, MEMBER),
            (OWNER, VIEWER, MEMBER),
            (ADMIN, MEMBER, VIEWER),
            (ADMIN, VIEWER, ADMIN),
        ],
    )
    def test_allowed(self, as_user, org, actor_role, target_role, new_role):
        actor, target = member(org, actor_role), member(org, target_role)

        response = as_user(actor.user, org).patch(detail_url(target), {"role": new_role})

        assert response.status_code == 200
        assert response.json()["role"] == new_role
        assert response.json()["user"]["email"] == target.user.email
        target.refresh_from_db()
        assert target.role == new_role

    @pytest.mark.parametrize(
        "actor_role,target_role",
        [
            (ADMIN, ADMIN),  # only the owner can change admins
            (ADMIN, OWNER),  # the owner is untouchable
            (OWNER, OWNER),  # ...even by themselves
            (MEMBER, VIEWER),  # members can't change roles
            (VIEWER, MEMBER),
        ],
    )
    def test_forbidden(self, as_user, org, actor_role, target_role):
        actor = member(org, actor_role)
        target = actor if (actor_role, target_role) == (OWNER, OWNER) else member(org, target_role)

        response = as_user(actor.user, org).patch(detail_url(target), {"role": VIEWER})

        assert response.status_code == 403
        target.refresh_from_db()
        assert target.role == target_role

    def test_cannot_promote_to_owner(self, as_user, org):
        target = member(org, ADMIN)
        response = as_user(org.owner, org).patch(detail_url(target), {"role": OWNER})
        assert response.status_code == 400
        assert "role" in response.json()
        target.refresh_from_db()
        assert target.role == ADMIN

    def test_invalid_role(self, as_user, org):
        target = member(org, MEMBER)
        response = as_user(org.owner, org).patch(detail_url(target), {"role": "boss"})
        assert response.status_code == 400

    def test_put_is_not_allowed(self, as_user, org):
        target = member(org, MEMBER)
        response = as_user(org.owner, org).put(detail_url(target), {"role": ADMIN})
        assert response.status_code == 405


@pytest.mark.django_db
class TestRemove:
    @pytest.mark.parametrize(
        "actor_role,target_role",
        [
            (OWNER, ADMIN),
            (OWNER, MEMBER),
            (OWNER, VIEWER),
            (ADMIN, MEMBER),
            (ADMIN, VIEWER),
        ],
    )
    def test_admins_can_remove(self, as_user, org, actor_role, target_role):
        actor, target = member(org, actor_role), member(org, target_role)
        response = as_user(actor.user, org).delete(detail_url(target))
        assert response.status_code == 204
        assert not Membership.objects.filter(id=target.id).exists()

    @pytest.mark.parametrize(
        "actor_role,target_role",
        [
            (ADMIN, ADMIN),
            (ADMIN, OWNER),
            (MEMBER, MEMBER),
            (MEMBER, VIEWER),
            (VIEWER, MEMBER),
        ],
    )
    def test_forbidden(self, as_user, org, actor_role, target_role):
        actor, target = member(org, actor_role), member(org, target_role)
        response = as_user(actor.user, org).delete(detail_url(target))
        assert response.status_code == 403
        assert Membership.objects.filter(id=target.id).exists()

    @pytest.mark.parametrize("role", [ADMIN, MEMBER, VIEWER])
    def test_anyone_but_owner_can_leave(self, as_user, org, role):
        me = member(org, role)
        response = as_user(me.user, org).delete(detail_url(me))
        assert response.status_code == 204
        assert not Membership.objects.filter(id=me.id).exists()

    def test_owner_cannot_leave(self, as_user, org):
        me = member(org, OWNER)
        response = as_user(me.user, org).delete(detail_url(me))
        assert response.status_code == 403
        assert Membership.objects.filter(id=me.id).exists()


@pytest.mark.django_db
class TestTenantIsolation:
    """A user from org B can't list, read, update or delete org A's members."""

    @pytest.fixture
    def setup(self, as_user):
        org_a, org_b = OrganizationFactory(), OrganizationFactory()
        a_member = member(org_a, MEMBER)
        return org_a, org_b, a_member

    def test_header_for_foreign_org_gets_404(self, as_user, setup):
        org_a, org_b, _ = setup
        assert as_user(org_b.owner, org_a).get(LIST_URL).status_code == 404

    def test_list_excludes_other_org(self, as_user, setup):
        org_a, org_b, a_member = setup
        ids = {row["id"] for row in as_user(org_b.owner, org_b).get(LIST_URL).json()["results"]}
        assert a_member.id not in ids
        assert ids == set(org_b.memberships.values_list("id", flat=True))

    def test_cannot_retrieve(self, as_user, setup):
        org_a, org_b, a_member = setup
        assert as_user(org_b.owner, org_b).get(detail_url(a_member)).status_code == 404

    def test_cannot_update(self, as_user, setup):
        org_a, org_b, a_member = setup
        response = as_user(org_b.owner, org_b).patch(detail_url(a_member), {"role": ADMIN})
        assert response.status_code == 404
        a_member.refresh_from_db()
        assert a_member.role == MEMBER

    def test_cannot_delete(self, as_user, setup):
        org_a, org_b, a_member = setup
        assert as_user(org_b.owner, org_b).delete(detail_url(a_member)).status_code == 404
        assert Membership.objects.filter(id=a_member.id).exists()
