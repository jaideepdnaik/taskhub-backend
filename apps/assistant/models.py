"""Ask TaskHub: searchable knowledge chunks and saved assistant conversations."""
from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.db import models
from pgvector.django import HnswIndex, VectorField

from apps.core.models import TimeStampedModel
from apps.organizations.models import Organization
from apps.projects.models import Project

# Fixed by the migration. Changing it means a new migration and a full reindex.
EMBEDDING_DIMENSIONS = 1024


class ChunkSource(models.TextChoices):
    PROJECT = "project", "Project"
    TASK = "task", "Task"
    COMMENT = "comment", "Comment"


class KnowledgeChunk(TimeStampedModel):
    """A piece of org content, embedded for retrieval.

    Only meaning-bearing text is embedded (titles, descriptions, labels, comment
    bodies). Fast-changing fields such as status or assignee are read live from
    the task at question time, so moving a card never makes a chunk stale.

    Every source is a real FK with CASCADE, so deleting a project, task or
    comment removes its chunks without extra cleanup code.
    """

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="+")
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="knowledge_chunks"
    )
    task = models.ForeignKey(
        "tasks.Task", on_delete=models.CASCADE, null=True, blank=True, related_name="+"
    )
    comment = models.ForeignKey(
        "tasks.Comment", on_delete=models.CASCADE, null=True, blank=True, related_name="+"
    )
    source_type = models.CharField(max_length=10, choices=ChunkSource.choices)
    # "task:12", "comment:7", "project:3"; unique together with chunk_index.
    source_key = models.CharField(max_length=40)
    chunk_index = models.PositiveSmallIntegerField(default=0)
    content = models.TextField()
    content_hash = models.CharField(max_length=64)
    embedding = VectorField(dimensions=EMBEDDING_DIMENSIONS)
    search_vector = models.GeneratedField(
        expression=SearchVector("content", config="english"),
        output_field=SearchVectorField(),
        db_persist=True,
    )

    class Meta:
        ordering = ["source_key", "chunk_index"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_key", "chunk_index"], name="unique_chunk_per_source"
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "project"]),
            HnswIndex(
                name="chunk_embedding_hnsw",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
            GinIndex(fields=["search_vector"], name="chunk_search_gin"),
        ]

    def __str__(self):
        return f"{self.source_key}#{self.chunk_index}"


class Conversation(TimeStampedModel):
    """An Ask TaskHub thread. Private to the user who started it."""

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="assistant_conversations"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="assistant_conversations",
    )
    # Optional: limit retrieval to one project ("ask about this project").
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="assistant_conversations",
    )
    title = models.CharField(max_length=200)

    class Meta:
        ordering = ["-updated_at", "-id"]
        indexes = [models.Index(fields=["organization", "user", "-updated_at"])]

    def __str__(self):
        return self.title


class MessageRole(models.TextChoices):
    USER = "user", "User"
    ASSISTANT = "assistant", "Assistant"


class Message(TimeStampedModel):
    """One turn in a conversation. Assistant turns carry citations and usage."""

    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="messages"
    )
    role = models.CharField(max_length=10, choices=MessageRole.choices)
    content = models.TextField()
    # [{"index": 1, "source_type": "task", "task_id": 4, "comment_id": null,
    #   "project_id": 2, "title": "...", "excerpt": "..."}]
    citations = models.JSONField(default=list, blank=True)
    model = models.CharField(max_length=100, blank=True)
    usage = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["conversation", "created_at"])]

    def __str__(self):
        return f"{self.role}: {self.content[:40]}"
