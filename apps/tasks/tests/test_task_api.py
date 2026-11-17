from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.organizations.models import Role
from apps.organizations.tests.factories import MembershipFactory, OrganizationFactory
from apps.projects.tests.factories import ProjectFactory
from apps.tasks.models import Task, TaskStatus
from apps.tasks.tests.factories import TaskFactory

OWNER, ADMIN, MEMBER, VIEWER = Role.OWNER, Role.ADMIN, Role.MEMBER, Role.VIEWER
TODO, IN_PROGRESS, REVIEW, DONE = (
    TaskStatus.TODO,
    TaskStatus.IN_PROGRESS,
    TaskStatus.REVIEW,
    TaskStatus.DONE,
)
TASKS_URL = "/api/tasks/"


def board_url(project):
    return f"/api/projects/{project.id}/tasks/"


def task_url(task, suffix=""):
    return f"/api/tasks/{task.id}/{suffix}"


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
    return [row["id"] for row in response.json()["results"]]


def column(project, status):
    """(title, position) pairs of a column, in board order."""
    return list(
        Task.objects.filter(project=project, status=status)
        .order_by("position")
        .values_list("title", "position")
    )


@pytest.mark.django_db
class TestVisibility:
    @pytest.mark.parametrize("role", [OWNER, ADMIN])
    def test_admins_see_all_org_tasks(self, as_user, org, role):
        t1 = TaskFactory(project=ProjectFactory(organization=org))
        t2 = TaskFactory(project=ProjectFactory(organization=org))
        assert set(ids(as_user(user_with_role(org, role), org).get(TASKS_URL))) == {t1.id, t2.id}

    @pytest.mark.parametrize("role", [MEMBER, VIEWER])
    def test_others_see_only_tasks_in_their_projects(self, as_user, org, role):
        user = user_with_role(org, role)
        mine = TaskFactory(project=ProjectFactory(organization=org, members=[user]))
        hidden_project = ProjectFactory(organization=org)
        hidden = TaskFactory(project=hidden_project)
        client = as_user(user, org)

        assert ids(client.get(TASKS_URL)) == [mine.id]
        assert client.get(task_url(mine)).status_code == 200
        assert client.get(task_url(hidden)).status_code == 404
        assert client.get(board_url(hidden_project)).status_code == 404

    def test_board_lists_only_that_project_in_column_order(self, as_user, org):
        project = ProjectFactory(organization=org)
        done = TaskFactory(project=project, status=DONE)
        todo2 = TaskFactory(project=project, title="second")
        todo1 = TaskFactory(project=project, title="first", position=0)
        todo2.position = 1
        todo2.save()
        review = TaskFactory(project=project, status=REVIEW)
        TaskFactory(project=ProjectFactory(organization=org))  # other project

        response = as_user(org.owner, org).get(board_url(project))

        assert response.status_code == 200
        # columns in board order (todo, in_progress, review, done), then position
        assert ids(response) == [todo1.id, todo2.id, review.id, done.id]

    def test_shape(self, as_user, org):
        member = user_with_role(org, MEMBER)
        project = ProjectFactory(organization=org, members=[member])
        task = TaskFactory(
            project=project,
            assignee=member,
            labels=["bug"],
            due_date=timezone.localdate() - timedelta(days=1),
        )

        data = as_user(member, org).get(task_url(task)).json()

        assert data["project"] == {"id": project.id, "name": project.name}
        assert data["assignee"]["email"] == member.email
        assert data["reporter"]["email"] == org.owner.email
        assert data["labels"] == ["bug"]
        assert data["status"] == TODO
        assert data["priority"] == "medium"
        assert data["is_overdue"] is True

    def test_list_query_count_does_not_grow(self, as_user, org):
        member = user_with_role(org, MEMBER)
        project = ProjectFactory(organization=org, members=[member])
        for _ in range(6):
            TaskFactory(project=project, assignee=member)
        client = as_user(org.owner, org)

        with CaptureQueriesContext(connection) as ctx:
            response = client.get(TASKS_URL)

        selects = [q for q in ctx.captured_queries if q["sql"].startswith("SELECT")]
        assert len(selects) == 3  # membership check + page count + rows (with joins)
        assert response.json()["count"] == 6


@pytest.mark.django_db
class TestCreate:
    @pytest.mark.parametrize("role", [OWNER, ADMIN, MEMBER])
    def test_editors_can_create(self, as_user, org, role):
        user = user_with_role(org, role)
        project = ProjectFactory(organization=org, members=[user])

        response = as_user(user, org).post(
            board_url(project),
            {"title": "  Fix login ", "priority": "high", "labels": ["bug", " bug ", "", "ui"]},
            format="json",
        )

        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "Fix login"
        assert data["status"] == TODO
        assert data["priority"] == "high"
        assert data["labels"] == ["bug", "ui"]
        assert data["reporter"]["email"] == user.email
        assert data["project"]["id"] == project.id
        assert data["assignee"] is None

    def test_new_tasks_go_to_bottom_of_their_column(self, as_user, org):
        project = ProjectFactory(organization=org)
        client = as_user(org.owner, org)

        positions = [
            client.post(board_url(project), {"title": f"T{i}"}).json()["position"] for i in range(3)
        ]
        review = client.post(board_url(project), {"title": "R", "status": REVIEW}).json()

        assert positions == [0, 1, 2]
        assert review["position"] == 0

    def test_viewer_gets_403(self, as_user, org):
        viewer = user_with_role(org, VIEWER)
        project = ProjectFactory(organization=org, members=[viewer])
        assert as_user(viewer, org).post(board_url(project), {"title": "X"}).status_code == 403
        assert not Task.objects.exists()

    def test_member_outside_project_gets_404(self, as_user, org):
        member = user_with_role(org, MEMBER)
        project = ProjectFactory(organization=org)
        assert as_user(member, org).post(board_url(project), {"title": "X"}).status_code == 404

    def test_assignee_must_be_project_member(self, as_user, org):
        in_project = user_with_role(org, MEMBER)
        org_only = user_with_role(org, MEMBER)
        project = ProjectFactory(organization=org, members=[in_project])
        client = as_user(org.owner, org)

        ok = client.post(board_url(project), {"title": "A", "assignee_id": in_project.id})
        bad = client.post(board_url(project), {"title": "B", "assignee_id": org_only.id})

        assert ok.status_code == 201
        assert ok.json()["assignee"]["id"] == in_project.id
        assert bad.status_code == 400
        assert "assignee_id" in bad.json()

    def test_server_controlled_fields_are_ignored(self, as_user, org):
        project = ProjectFactory(organization=org)
        other_project = ProjectFactory(organization=org)
        other_user = user_with_role(org, MEMBER)

        response = as_user(org.owner, org).post(
            board_url(project),
            {"title": "X", "project": other_project.id, "reporter": other_user.id, "position": 9},
        )

        task = Task.objects.get(id=response.json()["id"])
        assert (task.project, task.reporter, task.position) == (project, org.owner, 0)

    @pytest.mark.parametrize(
        "payload,field",
        [
            ({"title": "   "}, "title"),
            ({}, "title"),
            ({"title": "X", "status": "blocked"}, "status"),
            ({"title": "X", "priority": "meh"}, "priority"),
            ({"title": "X", "due_date": "tomorrow"}, "due_date"),
            ({"title": "X", "labels": ["a" * 51]}, "labels"),
            ({"title": "X", "labels": [str(i) for i in range(21)]}, "labels"),
        ],
    )
    def test_validation(self, as_user, org, payload, field):
        project = ProjectFactory(organization=org)
        response = as_user(org.owner, org).post(board_url(project), payload, format="json")
        assert response.status_code == 400
        assert field in response.json()

    def test_archived_project_is_read_only(self, as_user, org):
        project = ProjectFactory(organization=org, is_archived=True)
        response = as_user(org.owner, org).post(board_url(project), {"title": "X"})
        assert response.status_code == 400


@pytest.mark.django_db
class TestUpdate:
    def test_member_can_edit_any_task_in_their_project(self, as_user, org):
        member = user_with_role(org, MEMBER)
        project = ProjectFactory(organization=org, members=[member])
        task = TaskFactory(project=project)  # reported by the owner

        response = as_user(member, org).patch(
            task_url(task), {"title": "New", "assignee_id": member.id, "due_date": "2030-01-31"}
        )

        assert response.status_code == 200
        data = response.json()
        assert (data["title"], data["due_date"]) == ("New", "2030-01-31")
        assert data["assignee"]["id"] == member.id

    def test_unassign(self, as_user, org):
        member = user_with_role(org, MEMBER)
        task = TaskFactory(project=ProjectFactory(organization=org, members=[member]), assignee=member)
        response = as_user(org.owner, org).patch(task_url(task), {"assignee_id": None}, format="json")
        assert response.json()["assignee"] is None

    def test_viewer_gets_403(self, as_user, org):
        viewer = user_with_role(org, VIEWER)
        task = TaskFactory(project=ProjectFactory(organization=org, members=[viewer]), title="Old")
        assert as_user(viewer, org).patch(task_url(task), {"title": "New"}).status_code == 403
        task.refresh_from_db()
        assert task.title == "Old"

    def test_status_change_moves_to_bottom_and_closes_gap(self, as_user, org):
        project = ProjectFactory(organization=org)
        a, b, c = (TaskFactory(project=project, title=t) for t in "abc")
        TaskFactory(project=project, title="r0", status=REVIEW)

        response = as_user(org.owner, org).patch(task_url(a), {"status": REVIEW})

        assert response.status_code == 200
        assert response.json()["status"] == REVIEW
        assert column(project, TODO) == [("b", 0), ("c", 1)]
        assert column(project, REVIEW) == [("r0", 0), ("a", 1)]

    def test_put_is_not_allowed(self, as_user, org):
        task = TaskFactory(project=ProjectFactory(organization=org))
        assert as_user(org.owner, org).put(task_url(task), {"title": "X"}).status_code == 405

    def test_create_on_flat_endpoint_is_not_allowed(self, as_user, org):
        assert as_user(org.owner, org).post(TASKS_URL, {"title": "X"}).status_code == 405

    def test_archived_project_is_read_only(self, as_user, org):
        task = TaskFactory(project=ProjectFactory(organization=org, is_archived=True), title="Old")
        assert as_user(org.owner, org).patch(task_url(task), {"title": "New"}).status_code == 400


@pytest.mark.django_db
class TestDelete:
    @pytest.mark.parametrize("role", [OWNER, ADMIN])
    def test_admins_can_delete(self, as_user, org, role):
        task = TaskFactory(project=ProjectFactory(organization=org))
        assert as_user(user_with_role(org, role), org).delete(task_url(task)).status_code == 204
        assert not Task.objects.filter(id=task.id).exists()

    def test_reporter_can_delete(self, as_user, org):
        member = user_with_role(org, MEMBER)
        task = TaskFactory(project=ProjectFactory(organization=org, members=[member]), reporter=member)
        assert as_user(member, org).delete(task_url(task)).status_code == 204

    @pytest.mark.parametrize("role", [MEMBER, VIEWER])
    def test_others_get_403(self, as_user, org, role):
        user = user_with_role(org, role)
        task = TaskFactory(project=ProjectFactory(organization=org, members=[user]))
        assert as_user(user, org).delete(task_url(task)).status_code == 403
        assert Task.objects.filter(id=task.id).exists()

    def test_archived_project_is_read_only(self, as_user, org):
        task = TaskFactory(project=ProjectFactory(organization=org, is_archived=True))
        assert as_user(org.owner, org).delete(task_url(task)).status_code == 400


@pytest.mark.django_db
class TestMove:
    @pytest.fixture
    def board(self, org):
        project = ProjectFactory(organization=org)
        todo = [TaskFactory(project=project, title=t) for t in ("a", "b", "c")]
        review = [TaskFactory(project=project, title=t, status=REVIEW) for t in ("x", "y")]
        return project, todo, review

    def test_reorder_within_column(self, as_user, org, board):
        project, (a, b, c), _ = board
        response = as_user(org.owner, org).patch(
            task_url(c, "move/"), {"status": TODO, "position": 0}
        )
        assert response.status_code == 200
        assert response.json()["position"] == 0
        assert column(project, TODO) == [("c", 0), ("a", 1), ("b", 2)]

    def test_move_down_within_column(self, as_user, org, board):
        project, (a, b, c), _ = board
        as_user(org.owner, org).patch(task_url(a, "move/"), {"status": TODO, "position": 2})
        assert column(project, TODO) == [("b", 0), ("c", 1), ("a", 2)]

    def test_move_across_columns(self, as_user, org, board):
        project, (a, b, c), _ = board
        response = as_user(org.owner, org).patch(
            task_url(b, "move/"), {"status": REVIEW, "position": 1}
        )
        assert response.json()["status"] == REVIEW
        assert column(project, TODO) == [("a", 0), ("c", 1)]
        assert column(project, REVIEW) == [("x", 0), ("b", 1), ("y", 2)]

    def test_move_to_empty_column(self, as_user, org, board):
        project, (a, b, c), _ = board
        as_user(org.owner, org).patch(task_url(a, "move/"), {"status": DONE, "position": 0})
        assert column(project, DONE) == [("a", 0)]
        assert column(project, TODO) == [("b", 0), ("c", 1)]

    def test_position_past_end_is_clamped(self, as_user, org, board):
        project, (a, b, c), _ = board
        response = as_user(org.owner, org).patch(
            task_url(a, "move/"), {"status": REVIEW, "position": 99}
        )
        assert response.json()["position"] == 2
        assert column(project, REVIEW) == [("x", 0), ("y", 1), ("a", 2)]

    def test_other_projects_are_untouched(self, as_user, org, board):
        project, (a, b, c), _ = board
        other = TaskFactory(project=ProjectFactory(organization=org))
        as_user(org.owner, org).patch(task_url(c, "move/"), {"status": TODO, "position": 0})
        other.refresh_from_db()
        assert other.position == 0

    @pytest.mark.parametrize(
        "payload",
        [{"status": TODO, "position": -1}, {"status": "blocked", "position": 0}, {"position": 0}, {"status": TODO}],
    )
    def test_validation(self, as_user, org, board, payload):
        _, (a, b, c), _ = board
        assert as_user(org.owner, org).patch(task_url(a, "move/"), payload).status_code == 400

    def test_member_can_move(self, as_user, org):
        member = user_with_role(org, MEMBER)
        task = TaskFactory(project=ProjectFactory(organization=org, members=[member]))
        response = as_user(member, org).patch(task_url(task, "move/"), {"status": DONE, "position": 0})
        assert response.status_code == 200

    def test_viewer_gets_403(self, as_user, org):
        viewer = user_with_role(org, VIEWER)
        task = TaskFactory(project=ProjectFactory(organization=org, members=[viewer]))
        response = as_user(viewer, org).patch(task_url(task, "move/"), {"status": DONE, "position": 0})
        assert response.status_code == 403
        task.refresh_from_db()
        assert task.status == TODO

    def test_archived_project_is_read_only(self, as_user, org):
        task = TaskFactory(project=ProjectFactory(organization=org, is_archived=True))
        response = as_user(org.owner, org).patch(task_url(task, "move/"), {"status": DONE, "position": 0})
        assert response.status_code == 400


@pytest.mark.django_db
class TestFiltersSearchOrdering:
    @pytest.fixture
    def data(self, org):
        member = user_with_role(org, MEMBER)
        project = ProjectFactory(organization=org, members=[member])
        today = timezone.localdate()
        tasks = {
            "bug": TaskFactory(
                project=project,
                title="Login bug",
                priority="urgent",
                labels=["bug", "auth"],
                assignee=member,
                due_date=today - timedelta(days=2),
            ),
            "feature": TaskFactory(
                project=project,
                title="Dark mode",
                description="Theme toggle",
                priority="low",
                status=IN_PROGRESS,
                due_date=today + timedelta(days=5),
            ),
            "done_late": TaskFactory(
                project=project,
                title="Old chore",
                priority="high",
                status=DONE,
                assignee=member,
                due_date=today - timedelta(days=10),
            ),
        }
        return org, member, project, tasks

    def get_ids(self, as_user, data, **params):
        org, *_ = data
        return set(ids(as_user(org.owner, org).get(TASKS_URL, params)))

    def test_status_multiple(self, as_user, data):
        _, _, _, t = data
        assert self.get_ids(as_user, data, status=[TODO, DONE]) == {t["bug"].id, t["done_late"].id}

    def test_priority(self, as_user, data):
        _, _, _, t = data
        assert self.get_ids(as_user, data, priority="low") == {t["feature"].id}

    def test_assignee(self, as_user, data):
        org, member, _, t = data
        assert self.get_ids(as_user, data, assignee=member.id) == {t["bug"].id, t["done_late"].id}
        assert self.get_ids(as_user, data, assignee="none") == {t["feature"].id}
        assert self.get_ids(as_user, data, assignee="junk") == set()

    def test_assignee_me(self, as_user, data):
        org, member, _, t = data
        response = as_user(member, org).get(TASKS_URL, {"assignee": "me"})
        assert set(ids(response)) == {t["bug"].id, t["done_late"].id}

    def test_label(self, as_user, data):
        _, _, _, t = data
        assert self.get_ids(as_user, data, label="auth") == {t["bug"].id}

    def test_due_range(self, as_user, data):
        _, _, _, t = data
        today = timezone.localdate()
        assert self.get_ids(as_user, data, due_after=today.isoformat()) == {t["feature"].id}
        assert self.get_ids(as_user, data, due_before=today.isoformat()) == {
            t["bug"].id,
            t["done_late"].id,
        }

    def test_overdue_excludes_done(self, as_user, data):
        _, _, _, t = data
        assert self.get_ids(as_user, data, overdue="true") == {t["bug"].id}
        assert self.get_ids(as_user, data, overdue="false") == {t["feature"].id, t["done_late"].id}

    def test_project(self, as_user, data):
        org, _, project, t = data
        TaskFactory(project=ProjectFactory(organization=org))
        assert self.get_ids(as_user, data, project=project.id) == set(x.id for x in t.values())

    def test_search(self, as_user, data):
        _, _, _, t = data
        assert self.get_ids(as_user, data, search="theme") == {t["feature"].id}

    def test_order_by_priority_meaning(self, as_user, data):
        org, _, _, t = data
        response = as_user(org.owner, org).get(TASKS_URL, {"ordering": "-priority_rank"})
        assert ids(response) == [t["bug"].id, t["done_late"].id, t["feature"].id]

    def test_order_by_due_date(self, as_user, data):
        org, _, _, t = data
        response = as_user(org.owner, org).get(TASKS_URL, {"ordering": "due_date"})
        assert ids(response) == [t["done_late"].id, t["bug"].id, t["feature"].id]


@pytest.mark.django_db
class TestPagination:
    def test_default_and_custom_page_size(self, as_user, org):
        project = ProjectFactory(organization=org)
        Task.objects.bulk_create(
            [Task(project=project, title=f"T{i}", position=i) for i in range(25)]
        )
        client = as_user(org.owner, org)

        assert len(client.get(board_url(project)).json()["results"]) == 20
        assert len(client.get(board_url(project), {"page_size": 25}).json()["results"]) == 25
        assert client.get(board_url(project), {"page": 2}).json()["count"] == 25

    def test_page_size_is_capped(self, as_user, org):
        project = ProjectFactory(organization=org)
        Task.objects.bulk_create(
            [Task(project=project, title=f"T{i}", position=i) for i in range(105)]
        )
        response = as_user(org.owner, org).get(board_url(project), {"page_size": 500})
        assert len(response.json()["results"]) == 100


@pytest.mark.django_db
class TestUnassignOnAccessLoss:
    def test_removed_from_project(self, as_user, org):
        member = user_with_role(org, MEMBER)
        project = ProjectFactory(organization=org, members=[member])
        other_project = ProjectFactory(organization=org, members=[member])
        task = TaskFactory(project=project, assignee=member)
        kept = TaskFactory(project=other_project, assignee=member)

        response = as_user(org.owner, org).delete(f"/api/projects/{project.id}/members/{member.id}/")

        assert response.status_code == 204
        task.refresh_from_db()
        kept.refresh_from_db()
        assert task.assignee is None
        assert kept.assignee == member

    def test_reverse_removal_and_clear(self, org):
        member = user_with_role(org, MEMBER)
        p1 = ProjectFactory(organization=org, members=[member])
        p2 = ProjectFactory(organization=org, members=[member])
        t1 = TaskFactory(project=p1, assignee=member)
        t2 = TaskFactory(project=p2, assignee=member)

        member.projects.remove(p1)
        t1.refresh_from_db()
        t2.refresh_from_db()
        assert t1.assignee is None and t2.assignee == member

        p2.members.clear()
        t2.refresh_from_db()
        assert t2.assignee is None

    def test_left_org(self, as_user, org):
        membership = MembershipFactory(organization=org, role=MEMBER)
        task = TaskFactory(
            project=ProjectFactory(organization=org, members=[membership.user]),
            assignee=membership.user,
        )
        elsewhere = TaskFactory(
            project=ProjectFactory(members=[membership.user]), assignee=membership.user
        )

        as_user(membership.user, org).delete(f"/api/members/{membership.id}/")

        task.refresh_from_db()
        elsewhere.refresh_from_db()
        assert task.assignee is None
        assert elsewhere.assignee == membership.user


@pytest.mark.django_db
class TestTenantIsolation:
    """A user from org B can't list, read, create, update, move or delete org A's tasks."""

    @pytest.fixture
    def setup(self):
        org_a, org_b = OrganizationFactory(), OrganizationFactory()
        a_project = ProjectFactory(organization=org_a)
        return org_a, org_b, a_project, TaskFactory(project=a_project, title="A task")

    def test_header_for_foreign_org_gets_404(self, as_user, setup):
        org_a, org_b, a_project, _ = setup
        client = as_user(org_b.owner, org_a)
        assert client.get(TASKS_URL).status_code == 404
        assert client.post(board_url(a_project), {"title": "Sneaky"}).status_code == 404

    def test_lists_exclude_other_org(self, as_user, setup):
        org_a, org_b, a_project, a_task = setup
        b_task = TaskFactory(project=ProjectFactory(organization=org_b))
        client = as_user(org_b.owner, org_b)
        assert ids(client.get(TASKS_URL)) == [b_task.id]
        assert ids(client.get(TASKS_URL, {"project": a_project.id})) == []

    def test_cannot_use_foreign_project_board(self, as_user, setup):
        org_a, org_b, a_project, _ = setup
        client = as_user(org_b.owner, org_b)
        assert client.get(board_url(a_project)).status_code == 404
        assert client.post(board_url(a_project), {"title": "Sneaky"}).status_code == 404
        assert not Task.objects.filter(title="Sneaky").exists()

    def test_cannot_touch_foreign_task(self, as_user, setup):
        org_a, org_b, _, a_task = setup
        client = as_user(org_b.owner, org_b)

        assert client.get(task_url(a_task)).status_code == 404
        assert client.patch(task_url(a_task), {"title": "Pwned"}).status_code == 404
        assert client.patch(task_url(a_task, "move/"), {"status": DONE, "position": 0}).status_code == 404
        assert client.delete(task_url(a_task)).status_code == 404
        a_task.refresh_from_db()
        assert (a_task.title, a_task.status) == ("A task", TODO)

    def test_cannot_assign_foreign_user(self, as_user, setup):
        org_a, org_b, _, _ = setup
        b_project = ProjectFactory(organization=org_b)
        response = as_user(org_b.owner, org_b).post(
            board_url(b_project), {"title": "X", "assignee_id": org_a.owner.id}
        )
        assert response.status_code == 400
