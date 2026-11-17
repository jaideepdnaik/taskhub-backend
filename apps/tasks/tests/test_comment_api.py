import pytest

from apps.organizations.models import Role
from apps.organizations.tests.factories import MembershipFactory, OrganizationFactory
from apps.projects.tests.factories import ProjectFactory
from apps.tasks.models import ActivityLog, Comment
from apps.tasks.tests.factories import CommentFactory, TaskFactory

OWNER, ADMIN, MEMBER, VIEWER = Role.OWNER, Role.ADMIN, Role.MEMBER, Role.VIEWER


def comments_url(task):
    return f"/api/tasks/{task.id}/comments/"


def comment_url(comment):
    return f"/api/tasks/{comment.task_id}/comments/{comment.id}/"


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


def task_for(org, *members, **kwargs):
    return TaskFactory(project=ProjectFactory(organization=org, members=list(members), **kwargs))


@pytest.mark.django_db
class TestListAndRead:
    @pytest.mark.parametrize("role", [OWNER, ADMIN, MEMBER, VIEWER])
    def test_anyone_who_sees_the_task_can_read(self, as_user, org, role):
        user = user_with_role(org, role)
        task = task_for(org, user)
        first = CommentFactory(task=task, body="first")
        second = CommentFactory(task=task, body="second")
        CommentFactory()  # on another task

        response = as_user(user, org).get(comments_url(task))

        assert response.status_code == 200
        rows = response.json()["results"]
        assert [row["id"] for row in rows] == [first.id, second.id]  # oldest first
        assert rows[0]["author"]["email"] == org.owner.email
        assert rows[0]["edited_at"] is None

    def test_task_you_cannot_see_is_404(self, as_user, org):
        member = user_with_role(org, MEMBER)
        hidden = task_for(org)  # member isn't on this project
        comment = CommentFactory(task=hidden)
        client = as_user(member, org)
        assert client.get(comments_url(hidden)).status_code == 404
        assert client.get(comment_url(comment)).status_code == 404

    def test_comment_from_another_task_is_404(self, as_user, org):
        task = task_for(org)
        other = CommentFactory(task=task_for(org))
        url = f"/api/tasks/{task.id}/comments/{other.id}/"
        assert as_user(org.owner, org).get(url).status_code == 404

    def test_comment_count_on_tasks(self, as_user, org):
        task = task_for(org)
        CommentFactory.create_batch(3, task=task)
        data = as_user(org.owner, org).get(f"/api/tasks/{task.id}/").json()
        assert data["comment_count"] == 3


@pytest.mark.django_db
class TestCreate:
    @pytest.mark.parametrize("role", [OWNER, ADMIN, MEMBER])
    def test_editors_can_comment(self, as_user, org, role):
        user = user_with_role(org, role)
        task = task_for(org, user)

        response = as_user(user, org).post(comments_url(task), {"body": "  Looks good  "})

        assert response.status_code == 201
        data = response.json()
        assert data["body"] == "Looks good"
        assert data["author"]["email"] == user.email
        assert Comment.objects.get(id=data["id"]).task == task

    def test_viewer_gets_403(self, as_user, org):
        viewer = user_with_role(org, VIEWER)
        task = task_for(org, viewer)
        assert as_user(viewer, org).post(comments_url(task), {"body": "hi"}).status_code == 403
        assert not Comment.objects.exists()

    @pytest.mark.parametrize("body", ["", "   ", "x" * 5001])
    def test_invalid_body(self, as_user, org, body):
        task = task_for(org)
        response = as_user(org.owner, org).post(comments_url(task), {"body": body})
        assert response.status_code == 400
        assert "body" in response.json()

    def test_author_and_task_from_body_are_ignored(self, as_user, org):
        task = task_for(org)
        other_task = task_for(org)
        other_user = user_with_role(org, MEMBER)
        response = as_user(org.owner, org).post(
            comments_url(task), {"body": "hi", "author": other_user.id, "task": other_task.id}
        )
        comment = Comment.objects.get(id=response.json()["id"])
        assert (comment.task, comment.author) == (task, org.owner)

    def test_archived_project_is_read_only(self, as_user, org):
        task = task_for(org, is_archived=True)
        assert as_user(org.owner, org).post(comments_url(task), {"body": "hi"}).status_code == 400


@pytest.mark.django_db
class TestEdit:
    def test_author_can_edit_and_it_is_marked_edited(self, as_user, org):
        member = user_with_role(org, MEMBER)
        comment = CommentFactory(task=task_for(org, member), author=member, body="Old")

        response = as_user(member, org).patch(comment_url(comment), {"body": "New"})

        assert response.status_code == 200
        assert response.json()["body"] == "New"
        assert response.json()["edited_at"] is not None

    @pytest.mark.parametrize("role", [OWNER, ADMIN, MEMBER])
    def test_others_cannot_edit_even_admins(self, as_user, org, role):
        author = user_with_role(org, MEMBER)
        user = user_with_role(org, role)
        comment = CommentFactory(task=task_for(org, author, user), author=author, body="Old")

        response = as_user(user, org).patch(comment_url(comment), {"body": "Changed"})

        assert response.status_code == 403
        comment.refresh_from_db()
        assert comment.body == "Old"

    def test_author_demoted_to_viewer_cannot_edit(self, as_user, org):
        membership = MembershipFactory(organization=org, role=MEMBER)
        comment = CommentFactory(task=task_for(org, membership.user), author=membership.user)
        membership.role = VIEWER
        membership.save()
        response = as_user(membership.user, org).patch(comment_url(comment), {"body": "X"})
        assert response.status_code == 403

    def test_blank_edit_rejected(self, as_user, org):
        comment = CommentFactory(task=task_for(org))
        assert as_user(org.owner, org).patch(comment_url(comment), {"body": " "}).status_code == 400

    def test_put_is_not_allowed(self, as_user, org):
        comment = CommentFactory(task=task_for(org))
        assert as_user(org.owner, org).put(comment_url(comment), {"body": "X"}).status_code == 405

    def test_archived_project_is_read_only(self, as_user, org):
        comment = CommentFactory(task=task_for(org, is_archived=True))
        assert as_user(org.owner, org).patch(comment_url(comment), {"body": "X"}).status_code == 400


@pytest.mark.django_db
class TestDelete:
    def test_author_can_delete(self, as_user, org):
        member = user_with_role(org, MEMBER)
        comment = CommentFactory(task=task_for(org, member), author=member)
        assert as_user(member, org).delete(comment_url(comment)).status_code == 204
        assert not Comment.objects.filter(id=comment.id).exists()

    @pytest.mark.parametrize("role", [OWNER, ADMIN])
    def test_admins_can_delete_any(self, as_user, org, role):
        author = user_with_role(org, MEMBER)
        comment = CommentFactory(task=task_for(org, author), author=author)
        assert as_user(user_with_role(org, role), org).delete(comment_url(comment)).status_code == 204

    @pytest.mark.parametrize("role", [MEMBER, VIEWER])
    def test_others_cannot_delete(self, as_user, org, role):
        user = user_with_role(org, role)
        comment = CommentFactory(task=task_for(org, user))  # authored by the owner
        assert as_user(user, org).delete(comment_url(comment)).status_code == 403
        assert Comment.objects.filter(id=comment.id).exists()

    def test_archived_project_is_read_only(self, as_user, org):
        comment = CommentFactory(task=task_for(org, is_archived=True))
        assert as_user(org.owner, org).delete(comment_url(comment)).status_code == 400


@pytest.mark.django_db
class TestCommentActivity:
    def test_commenting_is_logged(self, as_user, org):
        task = task_for(org)
        comment_id = as_user(org.owner, org).post(comments_url(task), {"body": "hi"}).json()["id"]
        log = ActivityLog.objects.get(task=task)
        assert (log.action, log.actor, log.changes) == ("commented", org.owner, {"comment_id": comment_id})


@pytest.mark.django_db
class TestTenantIsolation:
    """A user from org B can't list, read, create, edit or delete org A's comments."""

    @pytest.fixture
    def setup(self):
        org_a, org_b = OrganizationFactory(), OrganizationFactory()
        a_task = task_for(org_a)
        return org_a, org_b, a_task, CommentFactory(task=a_task, body="A comment")

    def test_header_for_foreign_org_gets_404(self, as_user, setup):
        org_a, org_b, a_task, _ = setup
        assert as_user(org_b.owner, org_a).get(comments_url(a_task)).status_code == 404

    def test_cannot_touch_foreign_comments(self, as_user, setup):
        org_a, org_b, a_task, a_comment = setup
        client = as_user(org_b.owner, org_b)

        assert client.get(comments_url(a_task)).status_code == 404
        assert client.post(comments_url(a_task), {"body": "Sneaky"}).status_code == 404
        assert client.get(comment_url(a_comment)).status_code == 404
        assert client.patch(comment_url(a_comment), {"body": "Pwned"}).status_code == 404
        assert client.delete(comment_url(a_comment)).status_code == 404
        a_comment.refresh_from_db()
        assert a_comment.body == "A comment"
        assert not Comment.objects.filter(body="Sneaky").exists()
