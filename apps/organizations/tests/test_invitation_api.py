from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from django.utils import timezone

from apps.accounts.tests.factories import UserFactory
from apps.organizations.models import Invitation, Membership, Role
from apps.organizations.tests.factories import (
    InvitationFactory,
    MembershipFactory,
    OrganizationFactory,
)

LIST_URL = "/api/invitations/"
ACCEPT_URL = "/api/invitations/accept/"
OWNER, ADMIN, MEMBER, VIEWER = Role.OWNER, Role.ADMIN, Role.MEMBER, Role.VIEWER


def detail_url(invitation):
    return f"/api/invitations/{invitation.id}/"


def token_from(response):
    return parse_qs(urlparse(response.json()["accept_url"]).query)["token"][0]


@pytest.fixture
def org():
    return OrganizationFactory()


@pytest.fixture
def as_user(api_client):
    """Log in as `user`; if `org` is given, act on it via X-Org-ID."""

    def _as(user, org=None):
        api_client.force_authenticate(user=user)
        api_client.credentials(**({"HTTP_X_ORG_ID": str(org.id)} if org else {}))
        return api_client

    return _as


def user_with_role(org, role):
    if role == OWNER:
        return org.owner
    return MembershipFactory(organization=org, role=role).user


@pytest.mark.django_db
class TestCreate:
    @pytest.mark.parametrize("role", [OWNER, ADMIN])
    def test_admins_can_invite(self, as_user, org, role):
        inviter = user_with_role(org, role)

        response = as_user(inviter, org).post(
            LIST_URL, {"email": "Riya@Example.com", "role": VIEWER}
        )

        assert response.status_code == 201
        data = response.json()
        assert data["email"] == "riya@example.com"
        assert data["role"] == VIEWER
        assert data["status"] == "pending"
        assert data["invited_by"]["email"] == inviter.email
        invitation = Invitation.objects.get(id=data["id"])
        assert invitation.organization == org
        assert token_from(response) == invitation.token

    def test_role_defaults_to_member(self, as_user, org):
        response = as_user(org.owner, org).post(LIST_URL, {"email": "a@example.com"})
        assert response.json()["role"] == MEMBER

    @pytest.mark.parametrize("role", [MEMBER, VIEWER])
    def test_non_admins_get_403(self, as_user, org, role):
        response = as_user(user_with_role(org, role), org).post(
            LIST_URL, {"email": "a@example.com"}
        )
        assert response.status_code == 403
        assert not Invitation.objects.exists()

    def test_cannot_invite_as_owner(self, as_user, org):
        response = as_user(org.owner, org).post(
            LIST_URL, {"email": "a@example.com", "role": OWNER}
        )
        assert response.status_code == 400
        assert "role" in response.json()

    def test_invalid_email(self, as_user, org):
        response = as_user(org.owner, org).post(LIST_URL, {"email": "not-an-email"})
        assert response.status_code == 400
        assert "email" in response.json()

    def test_existing_member_is_rejected(self, as_user, org):
        existing = user_with_role(org, MEMBER)
        response = as_user(org.owner, org).post(LIST_URL, {"email": existing.email.upper()})
        assert response.status_code == 400
        assert "email" in response.json()

    def test_reinvite_refreshes_open_invite(self, as_user, org):
        old = InvitationFactory(
            organization=org,
            email="a@example.com",
            role=VIEWER,
            expires_at=timezone.now() + timedelta(days=1),
        )
        old_token, old_expiry = old.token, old.expires_at

        response = as_user(org.owner, org).post(
            LIST_URL, {"email": "A@example.com", "role": ADMIN}
        )

        assert response.status_code == 200
        assert response.json()["id"] == old.id
        old.refresh_from_db()
        assert old.role == ADMIN
        assert old.token != old_token
        assert old.expires_at > old_expiry
        assert Invitation.objects.count() == 1

    def test_reinvite_revives_expired_invite(self, as_user, org):
        old = InvitationFactory(organization=org, expires_at=timezone.now() - timedelta(days=1))

        response = as_user(org.owner, org).post(LIST_URL, {"email": old.email})

        assert response.status_code == 200
        assert response.json()["status"] == "pending"

    def test_new_invite_allowed_after_previous_was_accepted(self, as_user, org):
        # e.g. the person joined, left, and is being invited again
        InvitationFactory(organization=org, email="a@example.com", accepted_at=timezone.now())
        response = as_user(org.owner, org).post(LIST_URL, {"email": "a@example.com"})
        assert response.status_code == 201
        assert Invitation.objects.filter(email="a@example.com").count() == 2


@pytest.mark.django_db
class TestListAndRetrieve:
    def test_admin_lists_with_status_and_no_token(self, as_user, org):
        pending = InvitationFactory(organization=org)
        expired = InvitationFactory(
            organization=org, expires_at=timezone.now() - timedelta(minutes=1)
        )
        accepted = InvitationFactory(organization=org, accepted_at=timezone.now())

        response = as_user(org.owner, org).get(LIST_URL)

        assert response.status_code == 200
        rows = {row["id"]: row for row in response.json()["results"]}
        assert rows[pending.id]["status"] == "pending"
        assert rows[expired.id]["status"] == "expired"
        assert rows[accepted.id]["status"] == "accepted"
        for row in rows.values():
            assert "token" not in row
            assert "accept_url" not in row

    @pytest.mark.parametrize("status", ["pending", "expired", "accepted"])
    def test_filter_by_status(self, as_user, org, status):
        invites = {
            "pending": InvitationFactory(organization=org),
            "expired": InvitationFactory(
                organization=org, expires_at=timezone.now() - timedelta(minutes=1)
            ),
            "accepted": InvitationFactory(organization=org, accepted_at=timezone.now()),
        }
        response = as_user(org.owner, org).get(LIST_URL, {"status": status})
        assert [row["id"] for row in response.json()["results"]] == [invites[status].id]

    @pytest.mark.parametrize("role", [MEMBER, VIEWER])
    def test_non_admins_get_403(self, as_user, org, role):
        InvitationFactory(organization=org)
        assert as_user(user_with_role(org, role), org).get(LIST_URL).status_code == 403

    def test_retrieve(self, as_user, org):
        invitation = InvitationFactory(organization=org)
        response = as_user(org.owner, org).get(detail_url(invitation))
        assert response.status_code == 200
        assert response.json()["email"] == invitation.email


@pytest.mark.django_db
class TestRevoke:
    @pytest.mark.parametrize("role", [OWNER, ADMIN])
    def test_admins_can_revoke_pending(self, as_user, org, role):
        invitation = InvitationFactory(organization=org)
        response = as_user(user_with_role(org, role), org).delete(detail_url(invitation))
        assert response.status_code == 204
        assert not Invitation.objects.filter(id=invitation.id).exists()

    def test_accepted_cannot_be_revoked(self, as_user, org):
        invitation = InvitationFactory(organization=org, accepted_at=timezone.now())
        response = as_user(org.owner, org).delete(detail_url(invitation))
        assert response.status_code == 400
        assert Invitation.objects.filter(id=invitation.id).exists()

    def test_member_gets_403(self, as_user, org):
        invitation = InvitationFactory(organization=org)
        response = as_user(user_with_role(org, MEMBER), org).delete(detail_url(invitation))
        assert response.status_code == 403


@pytest.mark.django_db
class TestAccept:
    def test_invitee_joins_with_invited_role(self, as_user, org):
        invitee = UserFactory(email="riya@example.com")
        invitation = InvitationFactory(organization=org, email=invitee.email, role=VIEWER)

        response = as_user(invitee).post(ACCEPT_URL, {"token": invitation.token})

        assert response.status_code == 201
        assert response.json() == {
            "organization": {"id": org.id, "name": org.name, "slug": org.slug},
            "role": VIEWER,
        }
        assert Membership.objects.get(organization=org, user=invitee).role == VIEWER
        invitation.refresh_from_db()
        assert invitation.accepted_at is not None

    def test_full_flow_invite_then_accept(self, as_user, org):
        invitee = UserFactory(email="riya@example.com")
        created = as_user(org.owner, org).post(LIST_URL, {"email": "Riya@Example.com"})

        response = as_user(invitee).post(ACCEPT_URL, {"token": token_from(created)})

        assert response.status_code == 201
        assert org.memberships.filter(user=invitee, role=MEMBER).exists()

    def test_email_match_ignores_case(self, as_user, org):
        invitee = UserFactory(email="Riya@example.com")  # local part keeps its case
        invitation = InvitationFactory(organization=org, email="riya@example.com")
        response = as_user(invitee).post(ACCEPT_URL, {"token": invitation.token})
        assert response.status_code == 201

    def test_wrong_user_gets_403(self, as_user, org):
        invitation = InvitationFactory(organization=org, email="riya@example.com")
        response = as_user(UserFactory(email="someone@example.com")).post(
            ACCEPT_URL, {"token": invitation.token}
        )
        assert response.status_code == 403
        assert not Membership.objects.filter(organization=org, user__email="someone@example.com").exists()
        invitation.refresh_from_db()
        assert invitation.accepted_at is None

    def test_expired_gets_400(self, as_user, org):
        invitee = UserFactory()
        invitation = InvitationFactory(
            organization=org, email=invitee.email, expires_at=timezone.now() - timedelta(minutes=1)
        )
        response = as_user(invitee).post(ACCEPT_URL, {"token": invitation.token})
        assert response.status_code == 400
        assert "expired" in response.json()["token"][0]

    def test_cannot_accept_twice(self, as_user, org):
        invitee = UserFactory()
        invitation = InvitationFactory(organization=org, email=invitee.email)
        client = as_user(invitee)
        assert client.post(ACCEPT_URL, {"token": invitation.token}).status_code == 201

        response = client.post(ACCEPT_URL, {"token": invitation.token})

        assert response.status_code == 400
        assert "already been used" in response.json()["token"][0]

    def test_unknown_token_gets_404(self, as_user):
        assert as_user(UserFactory()).post(ACCEPT_URL, {"token": "nope"}).status_code == 404

    def test_missing_token_gets_400(self, as_user):
        assert as_user(UserFactory()).post(ACCEPT_URL, {}).status_code == 400

    def test_already_member_gets_400(self, as_user, org):
        existing = user_with_role(org, VIEWER)
        invitation = InvitationFactory(organization=org, email=existing.email, role=ADMIN)
        response = as_user(existing).post(ACCEPT_URL, {"token": invitation.token})
        assert response.status_code == 400
        assert Membership.objects.get(organization=org, user=existing).role == VIEWER

    def test_anonymous_gets_401(self, api_client, org):
        invitation = InvitationFactory(organization=org)
        assert api_client.post(ACCEPT_URL, {"token": invitation.token}).status_code == 401


@pytest.mark.django_db
class TestTenantIsolation:
    """An admin of org B can't list, read, create in or revoke org A's invitations."""

    @pytest.fixture
    def setup(self):
        org_a, org_b = OrganizationFactory(), OrganizationFactory()
        return org_a, org_b, InvitationFactory(organization=org_a)

    def test_header_for_foreign_org_gets_404(self, as_user, setup):
        org_a, org_b, _ = setup
        client = as_user(org_b.owner, org_a)
        assert client.get(LIST_URL).status_code == 404
        assert client.post(LIST_URL, {"email": "x@example.com"}).status_code == 404
        assert not Invitation.objects.filter(email="x@example.com").exists()

    def test_list_excludes_other_org(self, as_user, setup):
        org_a, org_b, a_invite = setup
        InvitationFactory(organization=org_b)
        ids = [row["id"] for row in as_user(org_b.owner, org_b).get(LIST_URL).json()["results"]]
        assert a_invite.id not in ids
        assert len(ids) == 1

    def test_cannot_retrieve(self, as_user, setup):
        org_a, org_b, a_invite = setup
        assert as_user(org_b.owner, org_b).get(detail_url(a_invite)).status_code == 404

    def test_cannot_revoke(self, as_user, setup):
        org_a, org_b, a_invite = setup
        assert as_user(org_b.owner, org_b).delete(detail_url(a_invite)).status_code == 404
        assert Invitation.objects.filter(id=a_invite.id).exists()
