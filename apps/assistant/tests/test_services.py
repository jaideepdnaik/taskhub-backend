from unittest import mock

import pytest

from apps.assistant import llm, services
from apps.assistant.indexing import index_source
from apps.assistant.models import ChunkSource, Message, MessageRole
from apps.assistant.tests.factories import ConversationFactory, MessageFactory
from apps.organizations.models import Membership
from apps.organizations.tests.factories import OrganizationFactory
from apps.projects.tests.factories import ProjectFactory
from apps.tasks.tests.factories import TaskFactory


def test_title_from():
    assert services.title_from("  what   is late? ") == "what is late?"
    long = services.title_from("x" * 200)
    assert len(long) == services.TITLE_LENGTH and long.endswith("…")


@pytest.fixture
def org():
    return OrganizationFactory()


@pytest.fixture
def owner(org):
    return Membership.objects.get(user=org.owner, organization=org)


def indexed_task(**kwargs):
    task = TaskFactory(**kwargs)
    index_source(ChunkSource.TASK, task.pk)
    return task


@pytest.mark.django_db
class TestAsk:
    def test_answer_is_saved_with_citations(self, org, owner):
        task = indexed_task(project=ProjectFactory(organization=org), title="Migrate database")
        conversation = ConversationFactory(organization=org)

        reply = services.ask(conversation, owner, "database migration status?")

        assert reply.role == MessageRole.ASSISTANT
        assert reply.content == "The most relevant item is Migrate database [1]."
        assert reply.citations[0]["task_id"] == task.pk
        assert reply.model == "fake"
        assert [m.role for m in conversation.messages.all()] == ["user", "assistant"]

    def test_follow_up_uses_history_and_carries_sources(self, org, owner):
        project = ProjectFactory(organization=org)
        indexed_task(project=project, title="Migrate database")
        conversation = ConversationFactory(organization=org)
        first = services.ask(conversation, owner, "database migration?")

        with mock.patch.object(services, "retrieve", wraps=services.retrieve) as retrieve:
            with mock.patch.object(llm.FakeBackend, "generate", wraps=llm.FakeBackend().generate) as generate:
                services.ask(conversation, owner, "who owns it?")

        query = retrieve.call_args.args[1]
        assert query == "database migration?\nwho owns it?"
        assert retrieve.call_args.kwargs["include_chunk_ids"] == [first.citations[0]["chunk_id"]]
        sent = generate.call_args.args[1]
        assert [m["role"] for m in sent] == ["user", "assistant", "user"]
        assert sent[0]["content"] == "database migration?"  # history without old sources
        assert sent[-1]["content"].endswith("Question: who owns it?")

    def test_history_is_limited(self, org, owner):
        conversation = ConversationFactory(organization=org)
        MessageFactory.create_batch(15, conversation=conversation)
        with mock.patch.object(llm.FakeBackend, "generate", wraps=llm.FakeBackend().generate) as generate:
            services.ask(conversation, owner, "latest?")
        assert len(generate.call_args.args[1]) == services.HISTORY_TURNS + 1

    def test_project_scoped_conversation(self, org, owner):
        project = ProjectFactory(organization=org)
        indexed_task(project=ProjectFactory(organization=org), title="Other project item")
        conversation = ConversationFactory(organization=org, project=project)
        reply = services.ask(conversation, owner, "other project item")
        assert reply.citations == []

    def test_nothing_is_saved_when_generation_fails(self, org, owner):
        conversation = ConversationFactory(organization=org)
        with mock.patch.object(
            llm.FakeBackend, "generate", side_effect=llm.AssistantUnavailable("down")
        ):
            with pytest.raises(llm.AssistantUnavailable):
                services.ask(conversation, owner, "anything?")
        assert not Message.objects.exists()
