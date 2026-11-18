from unittest import mock

import pytest
from django.core.cache import cache
from rest_framework.throttling import ScopedRateThrottle

from apps.assistant import llm
from apps.assistant.indexing import index_source
from apps.assistant.models import ChunkSource, Conversation, Message
from apps.assistant.tests.factories import ConversationFactory, MessageFactory
from apps.organizations.models import Membership, Role
from apps.organizations.tests.factories import MembershipFactory, OrganizationFactory
from apps.projects.tests.factories import ProjectFactory
from apps.tasks.tests.factories import TaskFactory

URL = "/api/assistant/conversations/"


def detail_url(conversation):
    return f"{URL}{conversation.id}/"


def messages_url(conversation):
    return f"{URL}{conversation.id}/messages/"


@pytest.fixture(autouse=True)
def clear_throttle_history():
    cache.clear()  # DRF throttles keep request history in the cache
    yield
    cache.clear()


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


def indexed_task(**kwargs):
    task = TaskFactory(**kwargs)
    index_source(ChunkSource.TASK, task.pk)
    return task


@pytest.fixture
def spy_generate():
    """Record what the (fake) model receives."""
    with mock.patch.object(
        llm.FakeBackend, "generate", autospec=True, side_effect=llm.FakeBackend.generate
    ) as spy:
        yield spy


@pytest.mark.django_db
class TestStartConversation:
    def test_answers_with_citations(self, as_user, org):
        task = indexed_task(project=ProjectFactory(organization=org), title="Upgrade Postgres")

        response = as_user(org.owner, org).post(URL, {"question": " postgres upgrade? "})

        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "postgres upgrade?"
        assert data["project_id"] is None
        question, answer = data["messages"]
        assert question["role"] == "user" and question["content"] == "postgres upgrade?"
        assert answer["role"] == "assistant"
        assert answer["content"] == "The most relevant item is Upgrade Postgres [1]."
        assert answer["citations"] == [
            {
                "index": 1,
                "source_type": "task",
                "project_id": task.project_id,
                "task_id": task.pk,
                "comment_id": None,
                "title": "Upgrade Postgres",
                "excerpt": "Task: Upgrade Postgres",  # no description: the header stands in
            }
        ]
        conversation = Conversation.objects.get()
        assert conversation.organization == org and conversation.user == org.owner

    def test_project_scope(self, as_user, org):
        project = ProjectFactory(organization=org)
        response = as_user(org.owner, org).post(URL, {"question": "hi", "project_id": project.pk})
        assert response.status_code == 201
        assert response.json()["project_id"] == project.pk

    def test_invisible_or_foreign_project_is_rejected(self, as_user, org):
        member = MembershipFactory(organization=org).user
        hidden = ProjectFactory(organization=org)  # member isn't on it
        foreign = ProjectFactory()
        client = as_user(member, org)
        for project in (hidden, foreign):
            response = client.post(URL, {"question": "hi", "project_id": project.pk})
            assert response.status_code == 400
            assert response.json() == {"project_id": ["Project not found."]}
        assert not Conversation.objects.exists()

    @pytest.mark.parametrize("question", ["", "   ", "x" * 2001])
    def test_question_validation(self, as_user, org, question):
        response = as_user(org.owner, org).post(URL, {"question": question})
        assert response.status_code == 400
        assert "question" in response.json()

    def test_viewer_can_ask(self, as_user, org):
        viewer = MembershipFactory(organization=org, role=Role.VIEWER).user
        assert as_user(viewer, org).post(URL, {"question": "status?"}).status_code == 201

    def test_unavailable_model_saves_nothing(self, as_user, org):
        with mock.patch.object(
            llm.FakeBackend, "generate", side_effect=llm.AssistantUnavailable("down")
        ):
            response = as_user(org.owner, org).post(URL, {"question": "status?"})
        assert response.status_code == 503
        assert response.json()["detail"].startswith("The assistant is unavailable")
        assert not Conversation.objects.exists() and not Message.objects.exists()

    def test_refusal_is_422(self, as_user, org):
        with mock.patch.object(llm.FakeBackend, "generate", side_effect=llm.AssistantRefused("no")):
            response = as_user(org.owner, org).post(URL, {"question": "status?"})
        assert response.status_code == 422
        assert not Conversation.objects.exists()


@pytest.mark.django_db
class TestFollowUps:
    def test_follow_up_returns_question_and_answer(self, as_user, org):
        indexed_task(project=ProjectFactory(organization=org), title="Hire designer")
        client = as_user(org.owner, org)
        conversation_id = client.post(URL, {"question": "hiring?"}).json()["id"]
        conversation = Conversation.objects.get(pk=conversation_id)

        response = client.post(messages_url(conversation), {"question": "who is on it?"})

        assert response.status_code == 201
        data = response.json()
        assert data["question"]["content"] == "who is on it?"
        assert data["answer"]["role"] == "assistant"
        assert conversation.messages.count() == 4

    def test_follow_up_validation(self, as_user, org):
        conversation = ConversationFactory(organization=org)
        response = as_user(org.owner, org).post(messages_url(conversation), {})
        assert response.status_code == 400


@pytest.mark.django_db
class TestReadAndDelete:
    def test_list_only_my_conversations_in_this_org(self, as_user, org):
        member = MembershipFactory(organization=org).user
        mine = ConversationFactory(organization=org, user=member)
        ConversationFactory(organization=org)  # the owner's, same org
        other_org = OrganizationFactory()
        MembershipFactory(organization=other_org, user=member)
        ConversationFactory(organization=other_org, user=member)  # mine, other org

        response = as_user(member, org).get(URL)

        assert response.status_code == 200
        assert [row["id"] for row in response.json()["results"]] == [mine.id]

    def test_retrieve_with_messages(self, as_user, org):
        conversation = ConversationFactory(organization=org)
        MessageFactory(conversation=conversation, content="Q")
        MessageFactory(conversation=conversation, role="assistant", content="A")
        data = as_user(org.owner, org).get(detail_url(conversation)).json()
        assert [m["content"] for m in data["messages"]] == ["Q", "A"]

    def test_citations_to_projects_i_lost_access_to_are_hidden(self, as_user, org):
        member = MembershipFactory(organization=org).user
        kept = ProjectFactory(organization=org, members=[member])
        lost = ProjectFactory(organization=org, members=[member])
        conversation = ConversationFactory(organization=org, user=member)
        citation = {"source_type": "task", "task_id": 1, "comment_id": None, "title": "t", "excerpt": ""}
        MessageFactory(
            conversation=conversation,
            role="assistant",
            citations=[
                {**citation, "index": 1, "chunk_id": 1, "project_id": kept.pk},
                {**citation, "index": 2, "chunk_id": 2, "project_id": lost.pk},
            ],
        )
        lost.members.remove(member)

        data = as_user(member, org).get(detail_url(conversation)).json()

        assert [c["project_id"] for c in data["messages"][0]["citations"]] == [kept.pk]

    def test_delete(self, as_user, org):
        conversation = ConversationFactory(organization=org)
        MessageFactory(conversation=conversation)
        assert as_user(org.owner, org).delete(detail_url(conversation)).status_code == 204
        assert not Conversation.objects.exists() and not Message.objects.exists()

    def test_no_put_or_patch(self, as_user, org):
        conversation = ConversationFactory(organization=org)
        client = as_user(org.owner, org)
        assert client.patch(detail_url(conversation), {"title": "x"}).status_code == 405
        assert client.put(detail_url(conversation), {"title": "x"}).status_code == 405

    def test_leaving_the_org_deletes_my_conversations_there(self, org):
        membership = MembershipFactory(organization=org)
        ConversationFactory(organization=org, user=membership.user)
        kept = ConversationFactory(user=membership.user)  # another org (as its owner)
        membership.delete()
        assert list(Conversation.objects.all()) == [kept]


@pytest.mark.django_db
class TestTenantIsolation:
    def test_another_users_conversation_is_404(self, as_user, org):
        """Same org or another org: list, read, ask in and delete are all 404 / hidden."""
        theirs = ConversationFactory(organization=org)  # the owner's
        colleague = MembershipFactory(organization=org, role=Role.ADMIN).user
        outsider_org = OrganizationFactory()

        for user, user_org in ((colleague, org), (outsider_org.owner, outsider_org)):
            client = as_user(user, user_org)
            assert client.get(URL).json()["results"] == []
            assert client.get(detail_url(theirs)).status_code == 404
            assert client.post(messages_url(theirs), {"question": "?"}).status_code == 404
            assert client.delete(detail_url(theirs)).status_code == 404
        assert Conversation.objects.filter(pk=theirs.pk).exists()
        assert theirs.messages.count() == 0

    def test_org_header_rules(self, api_client, org):
        assert api_client.get(URL).status_code == 401
        api_client.force_authenticate(user=OrganizationFactory().owner)
        assert api_client.get(URL).status_code == 400  # no X-Org-ID
        api_client.credentials(HTTP_X_ORG_ID=str(org.id))
        assert api_client.get(URL).status_code == 404  # not a member

    def test_other_orgs_content_never_reaches_the_model(self, as_user, org, spy_generate):
        secret = indexed_task(title="Acquisition of Initech", description="Board approved $4M")
        indexed_task(project=ProjectFactory(organization=org), title="Office party")

        response = as_user(org.owner, org).post(URL, {"question": "Acquisition of Initech $4M"})

        prompt = spy_generate.call_args.args[2][-1]["content"]
        assert "Initech" in prompt.split("Question:")[1]  # only in the question itself
        assert "Board approved" not in prompt
        assert all(c["task_id"] != secret.pk for c in response.json()["messages"][1]["citations"])

    def test_members_never_get_hidden_projects_in_the_prompt(self, as_user, org, spy_generate):
        member = MembershipFactory(organization=org).user
        indexed_task(project=ProjectFactory(organization=org), title="Layoff plan", description="draft list")
        indexed_task(project=ProjectFactory(organization=org, members=[member]), title="Sprint goals")

        as_user(member, org).post(URL, {"question": "layoff plan draft list"})

        prompt = spy_generate.call_args.args[2][-1]["content"]
        assert "draft list" not in prompt.split("Question:")[0]


@pytest.mark.django_db
def test_questions_are_throttled_but_reads_are_not(as_user, org):
    conversation = ConversationFactory(organization=org)
    client = as_user(org.owner, org)
    with mock.patch.dict(ScopedRateThrottle.THROTTLE_RATES, {"assistant": "2/hour"}):
        assert client.post(URL, {"question": "one"}).status_code == 201
        assert client.post(messages_url(conversation), {"question": "two"}).status_code == 201
        throttled = client.post(URL, {"question": "three"})
        assert throttled.status_code == 429
        assert "Retry-After" in throttled.headers
        assert client.get(URL).status_code == 200
