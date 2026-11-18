"""Retrieval quality basics and, above all, tenant/project isolation."""
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.assistant.indexing import index_source
from apps.assistant.models import ChunkSource
from apps.assistant.retrieval import reciprocal_rank_fusion, retrieve
from apps.organizations.models import Membership, Role
from apps.organizations.tests.factories import MembershipFactory, OrganizationFactory
from apps.projects.tests.factories import ProjectFactory
from apps.tasks.models import TaskStatus
from apps.tasks.tests.factories import CommentFactory, TaskFactory


def indexed_task(**kwargs):
    task = TaskFactory(**kwargs)
    index_source(ChunkSource.TASK, task.pk)
    return task


def membership(user, org):
    return Membership.objects.get(user=user, organization=org)


def task_ids(sources):
    return [s.task_id for s in sources]


@pytest.fixture
def org():
    return OrganizationFactory()


@pytest.fixture
def owner(org):
    return membership(org.owner, org)


def test_rrf_rewards_items_ranked_by_both_lists():
    assert reciprocal_rank_fusion([1, 2, 3], [3, 4]) == [3, 1, 2, 4]
    assert reciprocal_rank_fusion([], []) == []


@pytest.mark.django_db
class TestRanking:
    def test_most_relevant_task_first(self, org, owner):
        project = ProjectFactory(organization=org)
        indexed_task(project=project, title="Billing page redesign")
        target = indexed_task(
            project=project, title="Login fails", description="OAuth token expires on mobile"
        )
        indexed_task(project=project, title="Write release notes")

        sources = retrieve(owner, "why does the oauth token expire on login")

        assert sources[0].task_id == target.pk
        assert [s.index for s in sources] == list(range(1, len(sources) + 1))

    def test_keyword_match_on_rare_term(self, org, owner):
        project = ProjectFactory(organization=org)
        for i in range(12):
            indexed_task(project=project, title=f"Generic chore {i}", description="misc work")
        target = indexed_task(project=project, title="Crash", description="Error ERR4711 on save")

        assert retrieve(owner, "ERR4711")[0].task_id == target.pk

    def test_k_limits_results(self, org, owner):
        project = ProjectFactory(organization=org)
        for i in range(5):
            indexed_task(project=project, title=f"Task about search {i}")
        assert len(retrieve(owner, "search", k=3)) == 3

    def test_empty_index(self, owner):
        assert retrieve(owner, "anything") == []

    def test_live_task_details(self, org, owner):
        assignee = MembershipFactory(organization=org).user
        task = indexed_task(
            project=ProjectFactory(organization=org, is_archived=True),
            title="Renew SSL cert",
            assignee=assignee,
            due_date=timezone.localdate() - timedelta(days=1),
        )
        task.status = TaskStatus.REVIEW  # changes after indexing are still reflected
        task.save(update_fields=["status"])

        [source] = retrieve(owner, "ssl cert")

        assert source.title == "Renew SSL cert"
        assert source.details == {
            "project": "archived",
            "status": "Review",
            "priority": "Medium",
            "assignee": assignee.get_full_name(),
            "due_date": task.due_date.isoformat(),
            "overdue": "yes",
        }
        citation = source.citation()
        assert citation["task_id"] == task.pk and citation["project_id"] == task.project_id

    def test_comment_and_project_sources(self, org, owner):
        project = ProjectFactory(organization=org, name="Payments", description="Stripe")
        index_source(ChunkSource.PROJECT, project.pk)
        comment = CommentFactory(task=TaskFactory(project=project), body="Stripe webhook retries")
        index_source(ChunkSource.COMMENT, comment.pk)

        by_type = {s.source_type: s for s in retrieve(owner, "stripe")}

        assert by_type["project"].title == "Payments" and by_type["project"].task_id is None
        assert by_type["comment"].comment_id == comment.pk
        assert by_type["comment"].details["comment_by"] == org.owner.get_full_name()
        assert by_type["comment"].citation()["excerpt"] == "Stripe webhook retries"

    def test_query_count_is_constant(self, org, owner, django_assert_max_num_queries):
        project = ProjectFactory(organization=org)
        for i in range(8):
            CommentFactory(task=indexed_task(project=project, title=f"Deploy step {i}"))
        for comment_task in project.tasks.all():
            index_source(ChunkSource.TASK, comment_task.pk)
        with django_assert_max_num_queries(8):
            retrieve(owner, "deploy step")


@pytest.mark.django_db
class TestIsolation:
    def test_other_orgs_content_is_never_retrieved(self, org, owner):
        mine = indexed_task(project=ProjectFactory(organization=org), title="Quarterly roadmap")
        theirs = indexed_task(title="Quarterly roadmap")  # identical text, other org

        sources = retrieve(owner, "Quarterly roadmap")

        assert task_ids(sources) == [mine.pk]
        other = membership(theirs.project.organization.owner, theirs.project.organization)
        assert task_ids(retrieve(other, "Quarterly roadmap")) == [theirs.pk]

    @pytest.mark.parametrize("role", [Role.MEMBER, Role.VIEWER])
    def test_members_only_see_their_projects(self, org, role):
        user = MembershipFactory(organization=org, role=role).user
        visible = indexed_task(project=ProjectFactory(organization=org, members=[user]), title="Plan launch")
        indexed_task(project=ProjectFactory(organization=org), title="Plan launch")

        assert task_ids(retrieve(membership(user, org), "plan launch")) == [visible.pk]

    def test_admin_sees_every_project(self, org):
        admin = MembershipFactory(organization=org, role=Role.ADMIN)
        a = indexed_task(project=ProjectFactory(organization=org), title="Plan launch")
        b = indexed_task(project=ProjectFactory(organization=org), title="Plan launch")
        assert set(task_ids(retrieve(admin, "plan launch"))) == {a.pk, b.pk}

    def test_removed_from_project_loses_access_immediately(self, org):
        user = MembershipFactory(organization=org).user
        project = ProjectFactory(organization=org, members=[user])
        indexed_task(project=project, title="Secret budget")
        project.members.remove(user)
        assert retrieve(membership(user, org), "secret budget") == []

    def test_project_scope(self, org, owner):
        project = ProjectFactory(organization=org)
        inside = indexed_task(project=project, title="Fix search")
        indexed_task(project=ProjectFactory(organization=org), title="Fix search")
        assert task_ids(retrieve(owner, "fix search", project=project)) == [inside.pk]

    def test_carried_sources_are_rechecked(self, org):
        user = MembershipFactory(organization=org).user
        project = ProjectFactory(organization=org, members=[user])
        task = indexed_task(project=project, title="Hiring plan")
        other_org_task = indexed_task(title="Unrelated words entirely")
        member = membership(user, org)
        carried = retrieve(member, "hiring plan")[0].chunk_id
        foreign = other_org_task.project.knowledge_chunks.get().pk

        sources = retrieve(member, "zebra", include_chunk_ids=[carried, foreign])
        assert task_ids(sources) == [task.pk]  # the foreign chunk is dropped

        project.members.remove(user)
        assert retrieve(member, "zebra", include_chunk_ids=[carried]) == []
