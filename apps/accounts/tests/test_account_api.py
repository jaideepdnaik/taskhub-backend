import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.tests.factories import UserFactory
from apps.organizations.models import Role
from apps.organizations.tests.factories import MembershipFactory, OrganizationFactory

User = get_user_model()

REGISTER_URL = "/api/auth/register/"
LOGIN_URL = "/api/auth/login/"
ME_URL = "/api/auth/me/"
STRONG_PASSWORD = "Tr1cky-horse-battery"


def register(client, **overrides):
    payload = {
        "email": "riya@example.com",
        "password": STRONG_PASSWORD,
        "first_name": "Riya",
        "last_name": "Shah",
        **overrides,
    }
    return client.post(REGISTER_URL, payload)


@pytest.mark.django_db
class TestRegister:
    def test_creates_user_and_returns_tokens(self, api_client):
        response = register(api_client)

        assert response.status_code == 201
        data = response.json()
        assert data["access"] and data["refresh"]
        assert data["user"]["email"] == "riya@example.com"
        assert data["user"]["full_name"] == "Riya Shah"
        assert data["user"]["organizations"] == []
        assert "password" not in data["user"]
        user = User.objects.get(email="riya@example.com")
        assert user.check_password(STRONG_PASSWORD)
        assert not user.is_staff

    def test_returned_access_token_works(self, api_client):
        access = register(api_client).json()["access"]
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        response = api_client.get(ME_URL)
        assert response.status_code == 200
        assert response.json()["email"] == "riya@example.com"

    def test_names_are_optional(self, api_client):
        response = api_client.post(
            REGISTER_URL, {"email": "a@example.com", "password": STRONG_PASSWORD}
        )
        assert response.status_code == 201

    def test_email_is_lowercased(self, api_client):
        register(api_client, email="  Riya@Example.COM ")
        assert User.objects.filter(email="riya@example.com").exists()

    def test_duplicate_email_ignoring_case(self, api_client):
        UserFactory(email="riya@example.com")
        response = register(api_client, email="RIYA@example.com")
        assert response.status_code == 400
        assert "email" in response.json()
        assert User.objects.filter(email__iexact="riya@example.com").count() == 1

    @pytest.mark.parametrize(
        "password",
        [
            "short1!",  # too short
            "password123",  # too common
            "1234567890123",  # all digits
            "riya@example.com",  # too similar to email
        ],
    )
    def test_weak_passwords_are_rejected(self, api_client, password):
        response = register(api_client, password=password)
        assert response.status_code == 400
        assert isinstance(response.json()["password"], list)
        assert not User.objects.filter(email="riya@example.com").exists()

    @pytest.mark.parametrize("missing", ["email", "password"])
    def test_required_fields(self, api_client, missing):
        payload = {"email": "a@example.com", "password": STRONG_PASSWORD}
        del payload[missing]
        response = api_client.post(REGISTER_URL, payload)
        assert response.status_code == 400
        assert missing in response.json()

    def test_invalid_email(self, api_client):
        response = register(api_client, email="nope")
        assert response.status_code == 400
        assert "email" in response.json()

    def test_cannot_set_staff_flags(self, api_client):
        register(api_client, is_staff=True, is_superuser=True)
        user = User.objects.get(email="riya@example.com")
        assert not user.is_staff and not user.is_superuser

    def test_stale_token_does_not_block_signup(self, api_client):
        api_client.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")
        assert register(api_client).status_code == 201


@pytest.mark.django_db
class TestLogin:
    def test_login_ignores_email_case(self, api_client):
        register(api_client)
        response = api_client.post(
            LOGIN_URL, {"email": "RIYA@Example.com", "password": STRONG_PASSWORD}
        )
        assert response.status_code == 200
        assert {"access", "refresh"} <= response.json().keys()

    def test_wrong_password(self, api_client):
        register(api_client)
        response = api_client.post(LOGIN_URL, {"email": "riya@example.com", "password": "nope"})
        assert response.status_code == 401


@pytest.mark.django_db
class TestMe:
    def test_returns_profile_and_my_orgs_with_roles(self, api_client):
        user = UserFactory(first_name="Riya", last_name="Shah")
        owned = OrganizationFactory(name="Acme", owner=user)
        joined = OrganizationFactory(name="Beta")
        MembershipFactory(user=user, organization=joined, role=Role.VIEWER)
        OrganizationFactory(name="Not mine")
        api_client.force_authenticate(user=user)

        response = api_client.get(ME_URL)

        assert response.status_code == 200
        data = response.json()
        assert data["email"] == user.email
        assert data["full_name"] == "Riya Shah"
        assert data["organizations"] == [
            {"id": owned.id, "name": "Acme", "slug": owned.slug, "role": Role.OWNER},
            {"id": joined.id, "name": "Beta", "slug": joined.slug, "role": Role.VIEWER},
        ]

    def test_query_count_does_not_grow_with_orgs(self, api_client):
        user = UserFactory()
        for _ in range(5):
            MembershipFactory(user=user)
        api_client.force_authenticate(user=user)

        with CaptureQueriesContext(connection) as ctx:
            response = api_client.get(ME_URL)

        selects = [q for q in ctx.captured_queries if q["sql"].startswith("SELECT")]
        assert len(selects) == 2  # user + memberships with their orgs
        assert len(response.json()["organizations"]) == 5

    def test_patch_names(self, api_client):
        user = UserFactory(first_name="Old")
        api_client.force_authenticate(user=user)

        response = api_client.patch(ME_URL, {"first_name": "New", "last_name": "Name"})

        assert response.status_code == 200
        assert response.json()["full_name"] == "New Name"
        user.refresh_from_db()
        assert (user.first_name, user.last_name) == ("New", "Name")

    def test_email_and_id_are_read_only(self, api_client):
        user = UserFactory(email="riya@example.com")
        api_client.force_authenticate(user=user)

        api_client.patch(ME_URL, {"email": "hacker@example.com", "id": 999})

        user.refresh_from_db()
        assert user.email == "riya@example.com"
        assert user.id != 999

    def test_put_is_not_allowed(self, api_client):
        api_client.force_authenticate(user=UserFactory())
        assert api_client.put(ME_URL, {"first_name": "X"}).status_code == 405

    def test_anonymous_gets_401(self, api_client):
        assert api_client.get(ME_URL).status_code == 401
        assert api_client.patch(ME_URL, {"first_name": "X"}).status_code == 401
