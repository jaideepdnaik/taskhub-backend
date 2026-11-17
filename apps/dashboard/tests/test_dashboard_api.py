from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.organizations.models import Role
from apps.organizations.tests.factories import MembershipFactory, OrganizationFactory
from apps.projects.tests.factories import ProjectFactory
from apps.tasks.models import ActivityLog, TaskStatus
from apps.tasks.tests.factories import TaskFactory

URL = "/api/dashboard/"
TODO, IN_PROGRESS, REVIEW, DONE = (
    TaskStatus.TODO,
    TaskStatus.IN_PROGRESS,
    TaskStatus.REVIEW,
    TaskStatus.DONE,
)


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


@pytest.mark.django_db
class TestCounts:
    @pytest.fixture
    def data(self, org):
        today = timezone.localdate()
        member = MembershipFactory(organization=org, role=Role.MEMBER).user
        MembershipFactory(organization=org, role=Role.VIEWER)
        project = ProjectFactory(organization=org, members=[org.owner, member])
        ProjectFactory(organization=org, is_archived=True)
        TaskFactory(
            project=project, assignee=org.owner, priority="urgent", due_date=today - timedelta(days=1)
        )  # overdue, mine
        TaskFactory(
            project=project, assignee=org.owner, status=IN_PROGRESS, due_date=today + timedelta(days=3)
        )  # due this week, mine
        TaskFactory(project=project, status=REVIEW, priority="high", due_date=today + timedelta(days=30))
        TaskFactory(
            project=project, assignee=member, status=DONE, priority="low", due_date=today - timedelta(days=5)
        )  # done: neither overdue nor open
        return member, project

    def test_full_shape(self, as_user, org, data):
        response = as_user(org.owner, org).get(URL)

        assert response.status_code == 200
        body = response.json()
        assert body["projects"] == {"active": 1, "archived": 1}
        assert body["tasks"] == {
            "total": 4,
            "open": 3,
            "overdue": 1,
            "due_this_week": 1,
            "unassigned": 1,
            "completed_last_7_days": 0,
            "by_status": {"todo": 1, "in_progress": 1, "review": 1, "done": 1},
            "by_priority": {"low": 1, "medium": 1, "high": 1, "urgent": 1},
        }
        assert body["my_tasks"] == {"open": 2, "overdue": 1, "due_this_week": 1}
        assert body["members"] == {
            "total": 3,
            "by_role": {"owner": 1, "admin": 0, "member": 1, "viewer": 1},
        }
        assert body["recent_activity"] == []

    def test_my_tasks_are_per_user(self, as_user, org, data):
        member, _ = data
        body = as_user(member, org).get(URL).json()
        assert body["my_tasks"] == {"open": 0, "overdue": 0, "due_this_week": 0}

    def test_tasks_in_archived_projects_are_excluded(self, as_user, org):
        archived = ProjectFactory(organization=org, is_archived=True)
        TaskFactory(project=archived)
        assert as_user(org.owner, org).get(URL).json()["tasks"]["total"] == 0

    def test_empty_org(self, as_user, org):
        body = as_user(org.owner, org).get(URL).json()
        assert body["tasks"]["total"] == 0
        assert body["tasks"]["by_status"] == {"todo": 0, "in_progress": 0, "review": 0, "done": 0}
        assert body["members"]["total"] == 1


@pytest.mark.django_db
class TestVisibility:
    def test_member_only_counts_their_projects(self, as_user, org):
        member = MembershipFactory(organization=org, role=Role.MEMBER).user
        mine = ProjectFactory(organization=org, members=[member])
        TaskFactory(project=mine)
        hidden = ProjectFactory(organization=org)
        TaskFactory.create_batch(3, project=hidden)
        ProjectFactory(organization=org, is_archived=True)  # not theirs either

        body = as_user(member, org).get(URL).json()

        assert body["projects"] == {"active": 1, "archived": 0}
        assert body["tasks"]["total"] == 1

    def test_project_filter(self, as_user, org):
        p1, p2 = ProjectFactory(organization=org), ProjectFactory(organization=org)
        TaskFactory(project=p1)
        TaskFactory.create_batch(2, project=p2)

        body = as_user(org.owner, org).get(URL, {"project": p2.id}).json()

        assert body["tasks"]["total"] == 2
        assert body["projects"] == {"active": 1, "archived": 0}

    def test_project_filter_works_for_archived_project(self, as_user, org):
        archived = ProjectFactory(organization=org, is_archived=True)
        TaskFactory(project=archived)
        body = as_user(org.owner, org).get(URL, {"project": archived.id}).json()
        assert body["tasks"]["total"] == 1
        assert body["projects"] == {"active": 0, "archived": 1}

    def test_project_filter_on_invisible_project_is_404(self, as_user, org):
        member = MembershipFactory(organization=org, role=Role.MEMBER).user
        hidden = ProjectFactory(organization=org)
        assert as_user(member, org).get(URL, {"project": hidden.id}).status_code == 404

    def test_bad_project_param_is_400(self, as_user, org):
        response = as_user(org.owner, org).get(URL, {"project": "abc"})
        assert response.status_code == 400
        assert "project" in response.json()

    def test_viewer_can_read(self, as_user, org):
        viewer = MembershipFactory(organization=org, role=Role.VIEWER).user
        assert as_user(viewer, org).get(URL).status_code == 200


@pytest.mark.django_db
class TestActivityBasedStats:
    def test_completed_last_7_days_and_recent_activity(self, as_user, org):
        project = ProjectFactory(organization=org)
        done_task = TaskFactory(project=project, title="Shipped")
        reopened = TaskFactory(project=project, title="Reopened")
        client = as_user(org.owner, org)

        client.patch(f"/api/tasks/{done_task.id}/move/", {"status": DONE, "position": 0})
        client.patch(f"/api/tasks/{done_task.id}/", {"title": "Shipped!"})  # extra activity
        client.patch(f"/api/tasks/{reopened.id}/", {"status": DONE})
        client.patch(f"/api/tasks/{reopened.id}/", {"status": TODO})  # no longer done

        body = client.get(URL).json()

        assert body["tasks"]["completed_last_7_days"] == 1
        latest = body["recent_activity"][0]
        assert latest["task"] == {"id": reopened.id, "title": "Reopened", "project_id": project.id}
        assert latest["action"] == "updated"
        assert latest["actor"]["email"] == org.owner.email

    def test_old_completions_do_not_count(self, as_user, org):
        task = TaskFactory(project=ProjectFactory(organization=org), status=DONE)
        log = ActivityLog.objects.create(
            task=task, actor=org.owner, action="moved", changes={"status": {"from": "todo", "to": "done"}}
        )
        ActivityLog.objects.filter(pk=log.pk).update(created_at=timezone.now() - timedelta(days=8))
        assert as_user(org.owner, org).get(URL).json()["tasks"]["completed_last_7_days"] == 0

    def test_recent_activity_is_capped_at_10(self, as_user, org):
        task = TaskFactory(project=ProjectFactory(organization=org))
        for i in range(12):
            ActivityLog.objects.create(task=task, actor=org.owner, action="updated", changes={"i": i})
        assert len(as_user(org.owner, org).get(URL).json()["recent_activity"]) == 10


@pytest.mark.django_db
def test_query_count_is_constant(as_user, org):
    project = ProjectFactory(organization=org)
    for _ in range(8):
        task = TaskFactory(project=project)
        ActivityLog.objects.create(task=task, actor=org.owner, action="created", changes={})
    client = as_user(org.owner, org)

    with CaptureQueriesContext(connection) as ctx:
        client.get(URL)

    selects = [q for q in ctx.captured_queries if q["sql"].startswith("SELECT")]
    # membership, project counts, task counts, completed, members, recent activity
    assert len(selects) == 6


@pytest.mark.django_db
class TestAccessAndIsolation:
    def test_anonymous_gets_401(self, api_client, org):
        api_client.credentials(HTTP_X_ORG_ID=str(org.id))
        assert api_client.get(URL).status_code == 401

    def test_missing_header_gets_400(self, api_client, org):
        api_client.force_authenticate(user=org.owner)
        assert api_client.get(URL).status_code == 400

    def test_other_org_header_gets_404(self, as_user):
        org_a, org_b = OrganizationFactory(), OrganizationFactory()
        assert as_user(org_b.owner, org_a).get(URL).status_code == 404

    def test_stats_never_include_other_orgs(self, as_user):
        org_a, org_b = OrganizationFactory(), OrganizationFactory()
        a_project = ProjectFactory(organization=org_a)
        TaskFactory.create_batch(3, project=a_project, assignee=org_a.owner)
        ActivityLog.objects.create(task=a_project.tasks.first(), actor=org_a.owner, action="created")

        client = as_user(org_b.owner, org_b)
        body = client.get(URL).json()

        assert body["tasks"]["total"] == 0
        assert body["projects"] == {"active": 0, "archived": 0}
        assert body["members"]["total"] == 1
        assert body["recent_activity"] == []
        assert client.get(URL, {"project": a_project.id}).status_code == 404

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_read_only(self, as_user, org, method):
        assert getattr(as_user(org.owner, org), method)(URL, {}).status_code == 405
