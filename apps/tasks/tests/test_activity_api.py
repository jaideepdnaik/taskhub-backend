import pytest

from apps.organizations.models import Role
from apps.organizations.tests.factories import MembershipFactory, OrganizationFactory
from apps.projects.tests.factories import ProjectFactory
from apps.tasks.models import ActivityLog
from apps.tasks.services import diff, snapshot
from apps.tasks.tests.factories import TaskFactory

MEMBER, VIEWER = Role.MEMBER, Role.VIEWER


def activity_url(task):
    return f"/api/tasks/{task.id}/activity/"


@pytest.fixture
def org():
    return OrganizationFactory()


@pytest.fixture
def client(api_client, org):
    """Logged in as the org owner, acting on `org`."""
    api_client.force_authenticate(user=org.owner)
    api_client.credentials(HTTP_X_ORG_ID=str(org.id))
    return api_client


@pytest.fixture
def project(org):
    return ProjectFactory(organization=org)


def logs(task):
    return list(ActivityLog.objects.filter(task=task).order_by("created_at", "id"))


@pytest.mark.django_db
class TestRecording:
    def test_create_is_logged(self, client, org, project):
        task_id = client.post(f"/api/projects/{project.id}/tasks/", {"title": "Fix login"}).json()["id"]
        [log] = logs(task_id)
        assert (log.action, log.actor, log.changes) == ("created", org.owner, {"title": "Fix login"})

    def test_update_records_only_changed_fields(self, client, project):
        task = TaskFactory(project=project, title="Old", priority="medium", labels=["a"])

        client.patch(
            f"/api/tasks/{task.id}/",
            {"title": "New", "priority": "medium", "labels": ["a", "b"]},
            format="json",
        )

        [log] = logs(task)
        assert log.action == "updated"
        assert log.changes == {
            "title": {"from": "Old", "to": "New"},
            "labels": {"from": ["a"], "to": ["a", "b"]},
        }

    def test_assignee_change_keeps_email(self, client, org):
        member = MembershipFactory(organization=org, role=MEMBER).user
        task = TaskFactory(project=ProjectFactory(organization=org, members=[member]))

        client.patch(f"/api/tasks/{task.id}/", {"assignee_id": member.id})

        [log] = logs(task)
        assert log.changes == {
            "assignee": {"from": None, "to": {"id": member.id, "email": member.email}}
        }

    def test_status_change_via_patch_is_logged(self, client, project):
        task = TaskFactory(project=project)
        client.patch(f"/api/tasks/{task.id}/", {"status": "review", "due_date": "2030-05-01"})
        [log] = logs(task)
        assert log.changes == {
            "status": {"from": "todo", "to": "review"},
            "due_date": {"from": None, "to": "2030-05-01"},
        }

    def test_noop_patch_is_not_logged(self, client, project):
        task = TaskFactory(project=project, title="Same")
        client.patch(f"/api/tasks/{task.id}/", {"title": "Same"})
        assert logs(task) == []

    def test_move_is_logged(self, client, project):
        TaskFactory(project=project, title="first")
        task = TaskFactory(project=project, title="second")

        client.patch(f"/api/tasks/{task.id}/move/", {"status": "review", "position": 0})

        [log] = logs(task)
        assert log.action == "moved"
        assert log.changes == {
            "status": {"from": "todo", "to": "review"},
            "position": {"from": 1, "to": 0},
        }

    def test_reorder_within_column_logs_only_position(self, client, project):
        TaskFactory(project=project)
        task = TaskFactory(project=project)
        client.patch(f"/api/tasks/{task.id}/move/", {"status": "todo", "position": 0})
        [log] = logs(task)
        assert log.changes == {"position": {"from": 1, "to": 0}}

    def test_move_to_same_place_is_not_logged(self, client, project):
        task = TaskFactory(project=project)
        client.patch(f"/api/tasks/{task.id}/move/", {"status": "todo", "position": 0})
        assert logs(task) == []

    def test_failed_request_logs_nothing(self, client, project):
        task = TaskFactory(project=project)
        response = client.patch(f"/api/tasks/{task.id}/", {"priority": "meh"})
        assert response.status_code == 400
        assert logs(task) == []


@pytest.mark.django_db
class TestActivityEndpoint:
    def test_newest_first_with_actor(self, client, org, project):
        task = TaskFactory(project=project)
        client.patch(f"/api/tasks/{task.id}/", {"title": "Renamed"})
        client.post(f"/api/tasks/{task.id}/comments/", {"body": "hi"})

        response = client.get(activity_url(task))

        assert response.status_code == 200
        rows = response.json()["results"]
        assert [row["action"] for row in rows] == ["commented", "updated"]
        assert rows[0]["actor"]["email"] == org.owner.email

    def test_viewer_on_project_can_read(self, api_client, org):
        viewer = MembershipFactory(organization=org, role=VIEWER).user
        task = TaskFactory(project=ProjectFactory(organization=org, members=[viewer]))
        api_client.force_authenticate(user=viewer)
        api_client.credentials(HTTP_X_ORG_ID=str(org.id))
        assert api_client.get(activity_url(task)).status_code == 200

    def test_member_off_project_gets_404(self, api_client, org):
        member = MembershipFactory(organization=org, role=MEMBER).user
        task = TaskFactory(project=ProjectFactory(organization=org))
        api_client.force_authenticate(user=member)
        api_client.credentials(HTTP_X_ORG_ID=str(org.id))
        assert api_client.get(activity_url(task)).status_code == 404

    @pytest.mark.parametrize("method", ["post", "patch", "put", "delete"])
    def test_read_only(self, client, project, method):
        task = TaskFactory(project=project)
        assert getattr(client, method)(activity_url(task), {}).status_code == 405

    def test_tenant_isolation(self, api_client):
        org_a, org_b = OrganizationFactory(), OrganizationFactory()
        a_task = TaskFactory(project=ProjectFactory(organization=org_a))
        api_client.force_authenticate(user=org_b.owner)

        api_client.credentials(HTTP_X_ORG_ID=str(org_b.id))
        assert api_client.get(activity_url(a_task)).status_code == 404
        api_client.credentials(HTTP_X_ORG_ID=str(org_a.id))
        assert api_client.get(activity_url(a_task)).status_code == 404


@pytest.mark.django_db
def test_snapshot_and_diff_helpers():
    task = TaskFactory(title="A", labels=["x"])
    before = snapshot(task)
    task.title = "B"
    assert diff(before, snapshot(task)) == {"title": {"from": "A", "to": "B"}}
    assert diff(before, before) == {}
