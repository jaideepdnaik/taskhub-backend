from unittest import mock

import pytest
from celery.exceptions import Retry
from django.contrib.postgres.search import SearchQuery
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from voyageai import error as voyage_error

from apps.assistant import embeddings
from apps.assistant.embeddings import FakeEmbedder, VoyageEmbedder, get_embedder
from apps.assistant.indexing import index_source, reindex, split_text
from apps.assistant.models import EMBEDDING_DIMENSIONS, ChunkSource, KnowledgeChunk
from apps.assistant.tasks import index_source as index_source_job
from apps.organizations.tests.factories import OrganizationFactory
from apps.projects.tests.factories import ProjectFactory
from apps.tasks.tests.factories import CommentFactory, TaskFactory

TASK, COMMENT, PROJECT = ChunkSource.TASK, ChunkSource.COMMENT, ChunkSource.PROJECT


@pytest.fixture(autouse=True)
def fresh_embedder():
    embeddings._embedder_for.cache_clear()
    yield
    embeddings._embedder_for.cache_clear()


@pytest.fixture
def embed_spy():
    """Count calls to embed_documents while still producing real fake vectors."""
    with mock.patch.object(
        FakeEmbedder, "embed_documents", autospec=True, side_effect=FakeEmbedder().embed_documents.__func__
    ) as spy:
        yield spy


def chunks(kind, pk):
    return list(KnowledgeChunk.objects.filter(source_key=f"{kind}:{pk}"))


class TestSplitText:
    def test_short_and_empty(self):
        assert split_text("  hello  ") == ["hello"]
        assert split_text("   ") == []

    def test_long_text_overlaps_and_breaks_at_spaces(self):
        text = " ".join(f"word{i}" for i in range(600))  # ~4.4k characters
        parts = split_text(text, size=1000, overlap=100)
        assert len(parts) > 3
        assert all(len(p) <= 1000 for p in parts)
        assert all(not p.endswith("wor") for p in parts)  # no words cut in half
        # Consecutive chunks share some text.
        assert parts[0].split()[-1] in parts[1]

    def test_text_without_spaces_still_splits(self):
        parts = split_text("x" * 2500, size=1000, overlap=100)
        assert "".join(parts).count("x") >= 2500


@pytest.mark.django_db
class TestIndexSource:
    def test_task_chunk_has_text_links_and_vector(self):
        task = TaskFactory(title="Fix login", description="OAuth token expires", labels=["auth"])

        assert index_source(TASK, task.pk) == "indexed"

        [chunk] = chunks(TASK, task.pk)
        assert chunk.content == "Task: Fix login\nLabels: auth\n\nOAuth token expires"
        assert chunk.organization_id == task.project.organization_id
        assert chunk.project_id == task.project_id
        assert chunk.task_id == task.pk and chunk.comment_id is None
        assert len(chunk.embedding) == EMBEDDING_DIMENSIONS
        # The generated tsvector column is searchable.
        assert KnowledgeChunk.objects.filter(search_vector=SearchQuery("oauth")).exists()

    def test_unchanged_text_is_not_re_embedded(self, embed_spy):
        task = TaskFactory(description="same")
        index_source(TASK, task.pk)
        assert index_source(TASK, task.pk) == "unchanged"
        assert embed_spy.call_count == 1

    def test_only_changed_chunks_are_re_embedded(self, embed_spy):
        task = TaskFactory(description=" ".join(f"w{i}" for i in range(800)))
        index_source(TASK, task.pk)
        count = len(chunks(TASK, task.pk))
        assert count > 1

        task.description += " tail"
        task.save()
        index_source(TASK, task.pk)

        texts = embed_spy.call_args.args[1]
        assert len(texts) < count  # earlier chunks kept their vectors
        assert texts[-1].endswith("tail")

    def test_shrinking_text_removes_stale_chunks(self):
        task = TaskFactory(description=" ".join(f"w{i}" for i in range(800)))
        index_source(TASK, task.pk)
        task.description = "short now"
        task.save()
        index_source(TASK, task.pk)
        assert [c.chunk_index for c in chunks(TASK, task.pk)] == [0]

    def test_comment_chunk_includes_task_title_and_follows_renames(self):
        comment = CommentFactory(task=TaskFactory(title="Old title"), body="Looks good")
        index_source(COMMENT, comment.pk)
        assert chunks(COMMENT, comment.pk)[0].content.startswith("Comment on task: Old title")

        comment.task.title = "New title"
        comment.task.save()
        index_source(TASK, comment.task.pk)  # re-checks the task's comments too

        [chunk] = chunks(COMMENT, comment.pk)
        assert chunk.content.startswith("Comment on task: New title")
        assert chunk.task_id == comment.task_id and chunk.comment_id == comment.pk

    def test_project_chunk(self):
        project = ProjectFactory(name="Payments", description="Stripe integration")
        index_source(PROJECT, project.pk)
        [chunk] = chunks(PROJECT, project.pk)
        assert chunk.content == "Project: Payments\n\nStripe integration"
        assert chunk.task_id is None

    def test_missing_source_clears_chunks(self):
        task = TaskFactory()
        index_source(TASK, task.pk)
        pk = task.pk
        task.delete()  # CASCADE already removed the chunks
        assert chunks(TASK, pk) == []
        assert index_source(TASK, pk) == "deleted"

    def test_deleting_a_project_cascades(self):
        task = TaskFactory()
        index_source(TASK, task.pk)
        index_source(PROJECT, task.project_id)
        task.project.delete()
        assert not KnowledgeChunk.objects.exists()

    def test_unknown_kind(self):
        with pytest.raises(ValueError):
            index_source("wiki", 1)


@pytest.mark.django_db
class TestSignals:
    def test_saves_schedule_indexing_after_commit(self, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            comment = CommentFactory(body="ship it")
        assert chunks(TASK, comment.task_id)
        assert chunks(COMMENT, comment.pk)
        assert chunks(PROJECT, comment.task.project_id)

    def test_nothing_runs_if_the_transaction_never_commits(self):
        task = TaskFactory()
        assert chunks(TASK, task.pk) == []

    def test_non_text_updates_are_skipped(self, django_capture_on_commit_callbacks):
        task = TaskFactory()
        with django_capture_on_commit_callbacks() as callbacks:
            task.save(update_fields=["status", "position", "updated_at"])
        assert callbacks == []
        with django_capture_on_commit_callbacks() as callbacks:
            task.save(update_fields=["title", "updated_at"])
        assert len(callbacks) == 1

    def test_skipped_when_voyage_key_missing(self, settings, django_capture_on_commit_callbacks):
        settings.ASSISTANT_EMBEDDER = "voyage"
        settings.VOYAGE_API_KEY = ""
        with django_capture_on_commit_callbacks() as callbacks:
            TaskFactory()
        assert callbacks == []


@pytest.mark.django_db
class TestReindex:
    def test_reindex_one_org(self):
        task = TaskFactory()
        CommentFactory(task=task)
        other = TaskFactory()

        counts = reindex(organization_id=task.project.organization_id)

        assert counts == {"project": 1, "task": 1}
        assert KnowledgeChunk.objects.filter(source_type=COMMENT).count() == 1
        assert not KnowledgeChunk.objects.filter(project=other.project).exists()

    def test_command(self, capsys):
        TaskFactory(project=ProjectFactory(organization=OrganizationFactory()))
        call_command("reindex_knowledge")
        assert "Indexed 1 projects and 1 tasks" in capsys.readouterr().out
        assert KnowledgeChunk.objects.count() == 2


@pytest.mark.django_db
def test_job_retries_on_voyage_rate_limit():
    with mock.patch(
        "apps.assistant.indexing.index_source", side_effect=voyage_error.RateLimitError("slow down")
    ):
        with pytest.raises(Retry):
            index_source_job.delay(TASK, 1)


class TestEmbedders:
    def test_fake_is_deterministic_and_normalised(self):
        fake = FakeEmbedder()
        a, b = fake.embed_documents(["login bug", "login bug"])
        assert a == b
        assert abs(sum(v * v for v in a) - 1) < 1e-9
        assert fake.embed_query("") [0] == 1.0  # empty text still gets a valid vector

    def test_voyage_needs_a_key(self, settings):
        settings.ASSISTANT_EMBEDDER = "voyage"
        settings.VOYAGE_API_KEY = ""
        with pytest.raises(ImproperlyConfigured):
            get_embedder()

    def test_unknown_backend(self, settings):
        settings.ASSISTANT_EMBEDDER = "nope"
        with pytest.raises(ImproperlyConfigured):
            get_embedder()

    def test_voyage_batches_and_input_types(self, settings):
        settings.VOYAGE_API_KEY = "test-key"
        settings.ASSISTANT_EMBED_MODEL = "voyage-4"
        with mock.patch("voyageai.Client") as client_cls:
            client = client_cls.return_value
            client.embed.side_effect = lambda texts, **kw: mock.Mock(
                embeddings=[[0.1] * EMBEDDING_DIMENSIONS for _ in texts]
            )
            embedder = VoyageEmbedder()
            vectors = embedder.embed_documents([f"t{i}" for i in range(70)])
            query = embedder.embed_query("q")

        assert len(vectors) == 70 and len(query) == EMBEDDING_DIMENSIONS
        calls = client.embed.call_args_list
        assert [len(c.args[0]) for c in calls] == [64, 6, 1]
        assert calls[0].kwargs == {
            "model": "voyage-4",
            "input_type": "document",
            "output_dimension": EMBEDDING_DIMENSIONS,
        }
        assert calls[-1].kwargs["input_type"] == "query"
        assert embedder.embed_documents([]) == []
