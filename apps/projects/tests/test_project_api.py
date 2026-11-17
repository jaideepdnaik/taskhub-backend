import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.tests.factories import UserFactory
from apps.organizations.models import Membership, Role
from apps.organizations.tests.factories import MembershipFactory, OrganizationFactory
from apps.projects.models import Project
from apps.projects.tests.factories import ProjectFactory

LIST_URL = "/api/projects/"
OWNER, ADMIN, MEMBER, VIEWER = Role.OWNER, Role.ADMIN, Role.MEMBER, Role.VIEWER


def detail_url(project, suffix=""):
    return f"/api/projects/{project.id}/{suffix}"


@pytest.fixture
def org():
    return OrganizationFactory()


@pytest.fixture
def as_user(api_client):
    def _as(user, org):
        api_client.force_authenticate(user=user)
        api_client.credentials(HTTP_X_ORG_ID=str(org.id))
        return api_client

    return _as


def user_with_role(org, role):
    if role == OWNER:
        return org.owner
    return MembershipFactory(organization=org, role=role).user


def ids(response):
    return {row["id"] for row in response.json()["results"]}


@pytest.mark.django_db
class TestVisibility:
    @pytest.mark.parametrize("role", [OWNER, ADMIN])
    def test_admins_see_every_project(self, as_user, org, role):
        p1, p2 = ProjectFactory(organization=org), ProjectFactory(organization=org)
        assert ids(as_user(user_with_role(org, role), org).get(LIST_URL)) == {p1.id, p2.id}

    @pytest.mark.parametrize("role", [MEMBER, VIEWER])
    def test_others_see_only_their_projects(self, as_user, org, role):
        user = user_with_role(org, role)
        mine = ProjectFactory(organization=org, members=[user])
        other = ProjectFactory(organization=org)
        client = as_user(user, org)

        assert ids(client.get(LIST_URL)) == {mine.id}
        assert client.get(detail_url(mine)).status_code == 200
        assert client.get(detail_url(other)).status_code == 404

    def test_list_shape(self, as_user, org):
        member = user_with_role(org, MEMBER)
        project = ProjectFactory(organization=org, members=[org.owner, member])

        row = as_user(member, org).get(LIST_URL).json()["results"][0]

        assert row["id"] == project.id
        assert row["member_count"] == 2
        assert row["is_member"] is True
        assert row["created_by"]["email"] == org.owner.email
        assert "members" not in row  # only on detail

    def test_detail_includes_members(self, as_user, org):
        member = user_with_role(org, MEMBER)
        project = ProjectFactory(organization=org, members=[member])

        data = as_user(org.owner, org).get(detail_url(project)).json()

        assert [m["email"] for m in data["members"]] == [member.email]
        assert data["is_member"] is False  # the owner sees it but isn't on it

    def test_list_query_count_does_not_grow(self, as_user, org):
        member = user_with_role(org, MEMBER)
        for _ in range(5):
            ProjectFactory(organization=org, members=[org.owner, member])
        client = as_user(org.owner, org)

        with CaptureQueriesContext(connection) as ctx:
            response = client.get(LIST_URL)

        selects = [q for q in ctx.captured_queries if q["sql"].startswith("SELECT")]
        # membership check + page count + rows
        assert len(selects) == 3
        assert response.json()["count"] == 5


@pytest.mark.django_db
class TestFiltersAndSearch:
    def test_archived_hidden_by_default(self, as_user, org):
        active = ProjectFactory(organization=org)
        archived = ProjectFactory(organization=org, is_archived=True)
        client = as_user(org.owner, org)

        assert ids(client.get(LIST_URL)) == {active.id}
        assert ids(client.get(LIST_URL, {"is_archived": "true"})) == {archived.id}
        assert ids(client.get(LIST_URL, {"is_archived": "false"})) == {active.id}
        assert client.get(detail_url(archived)).status_code == 200

    def test_filter_by_member(self, as_user, org):
        member = user_with_role(org, MEMBER)
        theirs = ProjectFactory(organization=org, members=[member])
        ProjectFactory(organization=org)
        assert ids(as_user(org.owner, org).get(LIST_URL, {"member": member.id})) == {theirs.id}

    def test_search(self, as_user, org):
        ProjectFactory(organization=org, name="Website redesign")
        app = ProjectFactory(organization=org, name="Mobile", description="iOS app")
        assert ids(as_user(org.owner, org).get(LIST_URL, {"search": "ios"})) == {app.id}


@pytest.mark.django_db
class TestCreate:
    @pytest.mark.parametrize("role", [OWNER, ADMIN])
    def test_admins_can_create(self, as_user, org, role):
        user = user_with_role(org, role)

        response = as_user(user, org).post(
            LIST_URL, {"name": "  Website ", "description": "New site"}
        )

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Website"
        assert data["is_archived"] is False
        assert data["created_by"]["email"] == user.email
        assert [m["email"] for m in data["members"]] == [user.email]
        assert data["is_member"] is True
        assert data["member_count"] == 1
        assert Project.objects.get(id=data["id"]).organization == org

    @pytest.mark.parametrize("role", [MEMBER, VIEWER])
    def test_others_get_403(self, as_user, org, role):
        response = as_user(user_with_role(org, role), org).post(LIST_URL, {"name": "X"})
        assert response.status_code == 403
        assert not Project.objects.exists()

    def test_organization_from_body_is_ignored(self, as_user, org):
        other_org = OrganizationFactory()
        response = as_user(org.owner, org).post(
            LIST_URL, {"name": "X", "organization": other_org.id}
        )
        assert Project.objects.get(id=response.json()["id"]).organization == org

    def test_duplicate_name_ignoring_case(self, as_user, org):
        ProjectFactory(organization=org, name="Website")
        response = as_user(org.owner, org).post(LIST_URL, {"name": "WEBSITE"})
        assert response.status_code == 400
        assert "name" in response.json()

    def test_same_name_allowed_in_another_org(self, as_user, org):
        ProjectFactory(organization=OrganizationFactory(), name="Website")
        assert as_user(org.owner, org).post(LIST_URL, {"name": "Website"}).status_code == 201

    def test_name_required(self, as_user, org):
        assert as_user(org.owner, org).post(LIST_URL, {}).status_code == 400


@pytest.mark.django_db
class TestUpdateAndDelete:
    @pytest.mark.parametrize("role", [OWNER, ADMIN])
    def test_admins_can_update(self, as_user, org, role):
        project = ProjectFactory(organization=org, name="Old")
        response = as_user(user_with_role(org, role), org).patch(
            detail_url(project), {"name": "New", "description": "Desc"}
        )
        assert response.status_code == 200
        assert response.json()["name"] == "New"
        assert "members" in response.json()
        project.refresh_from_db()
        assert (project.name, project.description) == ("New", "Desc")

    def test_keeping_own_name_is_not_a_duplicate(self, as_user, org):
        project = ProjectFactory(organization=org, name="Website")
        response = as_user(org.owner, org).patch(detail_url(project), {"name": "website"})
        assert response.status_code == 200

    def test_rename_to_sibling_name_is_rejected(self, as_user, org):
        ProjectFactory(organization=org, name="Website")
        project = ProjectFactory(organization=org, name="App")
        response = as_user(org.owner, org).patch(detail_url(project), {"name": "Website"})
        assert response.status_code == 400

    @pytest.mark.parametrize("role", [MEMBER, VIEWER])
    def test_project_members_cannot_update_or_delete(self, as_user, org, role):
        user = user_with_role(org, role)
        project = ProjectFactory(organization=org, name="Old", members=[user])
        client = as_user(user, org)

        assert client.patch(detail_url(project), {"name": "New"}).status_code == 403
        assert client.delete(detail_url(project)).status_code == 403
        project.refresh_from_db()
        assert project.name == "Old"

    def test_put_is_not_allowed(self, as_user, org):
        project = ProjectFactory(organization=org)
        assert as_user(org.owner, org).put(detail_url(project), {"name": "X"}).status_code == 405

    @pytest.mark.parametrize("role", [OWNER, ADMIN])
    def test_admins_can_delete(self, as_user, org, role):
        project = ProjectFactory(organization=org)
        response = as_user(user_with_role(org, role), org).delete(detail_url(project))
        assert response.status_code == 204
        assert not Project.objects.filter(id=project.id).exists()


@pytest.mark.django_db
class TestArchive:
    def test_archive_and_unarchive(self, as_user, org):
        project = ProjectFactory(organization=org)
        client = as_user(org.owner, org)

        response = client.post(detail_url(project, "archive/"))
        assert response.status_code == 200
        assert response.json()["is_archived"] is True

        response = client.post(detail_url(project, "unarchive/"))
        assert response.status_code == 200
        assert response.json()["is_archived"] is False

    def test_archiving_twice_is_harmless(self, as_user, org):
        project = ProjectFactory(organization=org, is_archived=True)
        response = as_user(org.owner, org).post(detail_url(project, "archive/"))
        assert response.status_code == 200
        assert response.json()["is_archived"] is True

    @pytest.mark.parametrize("role", [MEMBER, VIEWER])
    def test_non_admins_get_403(self, as_user, org, role):
        user = user_with_role(org, role)
        project = ProjectFactory(organization=org, members=[user])
        assert as_user(user, org).post(detail_url(project, "archive/")).status_code == 403
        project.refresh_from_db()
        assert project.is_archived is False

    def test_archived_project_is_read_only(self, as_user, org):
        member = user_with_role(org, MEMBER)
        project = ProjectFactory(organization=org, name="Old", is_archived=True)
        client = as_user(org.owner, org)

        assert client.patch(detail_url(project), {"name": "New"}).status_code == 400
        assert client.post(detail_url(project, "members/"), {"user_id": member.id}).status_code == 400
        assert client.delete(detail_url(project, f"members/{org.owner.id}/")).status_code == 400
        project.refresh_from_db()
        assert project.name == "Old"
        assert not project.members.exists()

    def test_archived_project_can_be_deleted(self, as_user, org):
        project = ProjectFactory(organization=org, is_archived=True)
        assert as_user(org.owner, org).delete(detail_url(project)).status_code == 204


@pytest.mark.django_db
class TestProjectMembers:
    def test_list_members(self, as_user, org):
        member = user_with_role(org, MEMBER)
        project = ProjectFactory(organization=org, members=[member])
        response = as_user(member, org).get(detail_url(project, "members/"))
        assert response.status_code == 200
        assert [m["email"] for m in response.json()] == [member.email]

    @pytest.mark.parametrize("role", [OWNER, ADMIN])
    def test_admins_can_add_org_members(self, as_user, org, role):
        target = user_with_role(org, VIEWER)
        project = ProjectFactory(organization=org)

        response = as_user(user_with_role(org, role), org).post(
            detail_url(project, "members/"), {"user_id": target.id}
        )

        assert response.status_code == 200
        assert target.email in [m["email"] for m in response.json()]
        assert project.members.filter(id=target.id).exists()

    def test_adding_twice_is_harmless(self, as_user, org):
        target = user_with_role(org, MEMBER)
        project = ProjectFactory(organization=org, members=[target])
        response = as_user(org.owner, org).post(
            detail_url(project, "members/"), {"user_id": target.id}
        )
        assert response.status_code == 200
        assert project.members.count() == 1

    def test_cannot_add_user_outside_org(self, as_user, org):
        outsider = UserFactory()
        project = ProjectFactory(organization=org)
        response = as_user(org.owner, org).post(
            detail_url(project, "members/"), {"user_id": outsider.id}
        )
        assert response.status_code == 400
        assert "user_id" in response.json()
        assert not project.members.exists()

    def test_admins_can_remove(self, as_user, org):
        target = user_with_role(org, MEMBER)
        project = ProjectFactory(organization=org, members=[target])
        response = as_user(org.owner, org).delete(detail_url(project, f"members/{target.id}/"))
        assert response.status_code == 204
        assert not project.members.filter(id=target.id).exists()

    @pytest.mark.parametrize("role", [MEMBER, VIEWER])
    def test_non_admins_cannot_add_or_remove(self, as_user, org, role):
        user = user_with_role(org, role)
        other = user_with_role(org, MEMBER)
        project = ProjectFactory(organization=org, members=[user, other])
        client = as_user(user, org)

        assert client.post(detail_url(project, "members/"), {"user_id": other.id}).status_code == 403
        assert client.delete(detail_url(project, f"members/{other.id}/")).status_code == 403
        assert project.members.count() == 2


@pytest.mark.django_db
class TestLeavingOrgRemovesFromProjects:
    def test_removed_member_is_dropped_from_projects(self, as_user, org):
        membership = MembershipFactory(organization=org, role=MEMBER)
        p1 = ProjectFactory(organization=org, members=[membership.user])
        p2 = ProjectFactory(organization=org, members=[membership.user])
        elsewhere = ProjectFactory(members=[membership.user])  # another org

        response = as_user(org.owner, org).delete(f"/api/members/{membership.id}/")

        assert response.status_code == 204
        assert not p1.members.exists() and not p2.members.exists()
        assert elsewhere.members.filter(id=membership.user.id).exists()

    def test_leaving_drops_from_projects(self, as_user, org):
        membership = MembershipFactory(organization=org, role=VIEWER)
        project = ProjectFactory(organization=org, members=[membership.user])

        as_user(membership.user, org).delete(f"/api/members/{membership.id}/")

        assert not project.members.exists()
        assert not Membership.objects.filter(id=membership.id).exists()


@pytest.mark.django_db
class TestTenantIsolation:
    """A user from org B can't list, read, update or delete org A's projects."""

    @pytest.fixture
    def setup(self):
        org_a, org_b = OrganizationFactory(), OrganizationFactory()
        return org_a, org_b, ProjectFactory(organization=org_a, name="A project")

    def test_header_for_foreign_org_gets_404(self, as_user, setup):
        org_a, org_b, _ = setup
        client = as_user(org_b.owner, org_a)
        assert client.get(LIST_URL).status_code == 404
        assert client.post(LIST_URL, {"name": "Sneaky"}).status_code == 404
        assert not Project.objects.filter(name="Sneaky").exists()

    def test_list_excludes_other_org(self, as_user, setup):
        org_a, org_b, a_project = setup
        b_project = ProjectFactory(organization=org_b)
        assert ids(as_user(org_b.owner, org_b).get(LIST_URL)) == {b_project.id}

    def test_cannot_retrieve_update_or_delete(self, as_user, setup):
        org_a, org_b, a_project = setup
        client = as_user(org_b.owner, org_b)

        assert client.get(detail_url(a_project)).status_code == 404
        assert client.patch(detail_url(a_project), {"name": "Pwned"}).status_code == 404
        assert client.delete(detail_url(a_project)).status_code == 404
        assert client.post(detail_url(a_project, "archive/")).status_code == 404
        assert client.get(detail_url(a_project, "members/")).status_code == 404
        a_project.refresh_from_db()
        assert (a_project.name, a_project.is_archived) == ("A project", False)

    def test_cannot_add_own_user_to_foreign_project(self, as_user, setup):
        org_a, org_b, a_project = setup
        response = as_user(org_b.owner, org_b).post(
            detail_url(a_project, "members/"), {"user_id": org_b.owner.id}
        )
        assert response.status_code == 404
        assert not a_project.members.exists()
