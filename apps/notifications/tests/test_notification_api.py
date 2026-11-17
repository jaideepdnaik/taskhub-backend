import pytest

from apps.accounts.tests.factories import UserFactory
from apps.notifications.models import Notification
from apps.notifications.tests.factories import NotificationFactory
from apps.organizations.models import Membership, Role
from apps.organizations.tests.factories import MembershipFactory, OrganizationFactory

LIST_URL = "/api/notifications/"


def url(notification, suffix=""):
    return f"/api/notifications/{notification.id}/{suffix}"


@pytest.fixture
def me(api_client):
    user = UserFactory()
    api_client.force_authenticate(user=user)
    return user


def mine(user, **kwargs):
    org = kwargs.pop("organization", None) or OrganizationFactory()
    Membership.objects.get_or_create(organization=org, user=user, defaults={"role": Role.MEMBER})
    return NotificationFactory(recipient=user, organization=org, **kwargs)


@pytest.mark.django_db
class TestList:
    def test_lists_only_mine_newest_first_without_org_header(self, api_client, me):
        older = mine(me)
        newer = mine(me)
        NotificationFactory()  # someone else's

        response = api_client.get(LIST_URL)

        assert response.status_code == 200
        rows = response.json()["results"]
        assert [row["id"] for row in rows] == [newer.id, older.id]
        assert rows[0]["organization"] == {
            "id": newer.organization.id,
            "name": newer.organization.name,
            "slug": newer.organization.slug,
        }
        assert rows[0]["is_read"] is False
        assert rows[0]["payload"]["task_title"] == "T"

    def test_filters(self, api_client, me):
        org = OrganizationFactory()
        read = mine(me, organization=org, is_read=True)
        unread = mine(me, organization=org)
        other_org = mine(me, type="comment_added")

        def ids(**params):
            return {row["id"] for row in api_client.get(LIST_URL, params).json()["results"]}

        assert ids(is_read="false") == {unread.id, other_org.id}
        assert ids(organization=org.id) == {read.id, unread.id}
        assert ids(type="comment_added") == {other_org.id}

    def test_anonymous_gets_401(self, api_client):
        assert api_client.get(LIST_URL).status_code == 401


@pytest.mark.django_db
class TestUnreadCount:
    def test_counts_unread(self, api_client, me):
        org = OrganizationFactory()
        mine(me, organization=org)
        mine(me, organization=org)
        mine(me, is_read=True)
        mine(me)
        NotificationFactory()  # someone else's

        assert api_client.get(f"{LIST_URL}unread-count/").json() == {"count": 3}
        assert api_client.get(f"{LIST_URL}unread-count/", {"organization": org.id}).json() == {
            "count": 2
        }


@pytest.mark.django_db
class TestMarkRead:
    def test_mark_one_read(self, api_client, me):
        notification = mine(me)

        response = api_client.post(url(notification, "read/"))

        assert response.status_code == 200
        assert response.json()["is_read"] is True
        assert response.json()["read_at"] is not None

    def test_marking_twice_keeps_first_read_at(self, api_client, me):
        notification = mine(me)
        first = api_client.post(url(notification, "read/")).json()["read_at"]
        second = api_client.post(url(notification, "read/")).json()["read_at"]
        assert first == second

    def test_read_all(self, api_client, me):
        org = OrganizationFactory()
        a = mine(me, organization=org)
        b = mine(me, organization=org)
        c = mine(me)
        theirs = NotificationFactory()

        response = api_client.post(f"{LIST_URL}read-all/", {}, QUERY_STRING=f"organization={org.id}")

        assert response.json() == {"updated": 2}
        assert set(Notification.objects.filter(is_read=True).values_list("id", flat=True)) == {
            a.id,
            b.id,
        }

        assert api_client.post(f"{LIST_URL}read-all/").json() == {"updated": 1}
        c.refresh_from_db()
        theirs.refresh_from_db()
        assert c.is_read is True
        assert theirs.is_read is False


@pytest.mark.django_db
class TestDelete:
    def test_dismiss(self, api_client, me):
        notification = mine(me)
        assert api_client.delete(url(notification)).status_code == 204
        assert not Notification.objects.filter(id=notification.id).exists()

    @pytest.mark.parametrize("method", ["put", "patch"])
    def test_cannot_edit(self, api_client, me, method):
        notification = mine(me)
        assert getattr(api_client, method)(url(notification), {"is_read": True}).status_code == 405

    def test_cannot_create(self, api_client, me):
        assert api_client.post(LIST_URL, {"type": "task_assigned"}).status_code == 405


@pytest.mark.django_db
class TestIsolation:
    """Nobody can read, mark or delete someone else's notifications."""

    def test_someone_elses_is_404(self, api_client, me):
        theirs = NotificationFactory()
        assert api_client.post(url(theirs, "read/")).status_code == 404
        assert api_client.delete(url(theirs)).status_code == 404
        theirs.refresh_from_db()
        assert theirs.is_read is False

    def test_leaving_org_deletes_its_notifications(self, api_client, me):
        org = OrganizationFactory()
        membership = MembershipFactory(organization=org, user=me, role=Role.MEMBER)
        gone = NotificationFactory(recipient=me, organization=org)
        kept = mine(me)  # another org

        api_client.credentials(HTTP_X_ORG_ID=str(org.id))
        assert api_client.delete(f"/api/members/{membership.id}/").status_code == 204

        assert not Notification.objects.filter(id=gone.id).exists()
        assert Notification.objects.filter(id=kept.id).exists()
