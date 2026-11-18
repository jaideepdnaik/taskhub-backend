"""Hybrid retrieval for Ask TaskHub, limited to what the asking user may see.

The tenant and visibility filter is applied to the base queryset *before* any
ranking. Chunks from other orgs, or from projects the user isn't on, can never
be ranked, returned or sent to the model.

Ranking merges two lists with Reciprocal Rank Fusion (RRF):
  - vector similarity (pgvector cosine distance) for meaning;
  - Postgres full-text search for exact words (names, labels, error codes).
"""
from dataclasses import dataclass, field

from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db import connection, transaction
from django.db.models import F
from pgvector.django import CosineDistance

from apps.projects.models import Project

from .embeddings import get_embedder
from .models import KnowledgeChunk

TOP_K = 8
CANDIDATES = 30  # per ranking list, before fusion
RRF_K = 60  # the usual RRF constant: dampens the weight of top ranks


@dataclass
class Source:
    """One retrieved chunk, plus live details of its task for the prompt."""

    chunk_id: int
    source_type: str
    project_id: int
    project_name: str
    content: str
    task_id: int | None = None
    comment_id: int | None = None
    title: str = ""
    details: dict = field(default_factory=dict)
    index: int = 0  # 1-based number the model cites as [n]

    def citation(self):
        """The JSON stored on the assistant Message and returned to the client."""
        return {
            "index": self.index,
            "chunk_id": self.chunk_id,
            "source_type": self.source_type,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "comment_id": self.comment_id,
            "title": self.title,
            "excerpt": _excerpt(self.content),
        }


def _excerpt(content, limit=200):
    body = content.split("\n\n", 1)[-1].strip()  # drop the "Task: ..." header
    return body if len(body) <= limit else body[: limit - 1].rstrip() + "…"


def visible_chunks(membership, project=None):
    """Chunks this member may see: their org, and only projects visible to them."""
    chunks = KnowledgeChunk.objects.filter(
        organization_id=membership.organization_id,
        project__in=Project.objects.visible_to(membership),
    )
    if project is not None:
        chunks = chunks.filter(project=project)
    return chunks


def reciprocal_rank_fusion(*rankings):
    """Merge ranked id lists: score(id) = sum of 1 / (RRF_K + rank)."""
    scores = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
    return sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))


def _vector_ranking(chunks, query):
    vector = get_embedder().embed_query(query)
    with transaction.atomic():
        # pgvector >= 0.8: keep scanning the HNSW index until enough rows pass the
        # tenant filter, instead of returning too few results for small orgs.
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL hnsw.iterative_scan = 'relaxed_order'")
        return list(
            chunks.annotate(distance=CosineDistance("embedding", vector))
            .order_by("distance", "id")
            .values_list("id", flat=True)[:CANDIDATES]
        )


def _keyword_ranking(chunks, query):
    tsquery = SearchQuery(query, config="english", search_type="websearch")
    return list(
        chunks.filter(search_vector=tsquery)
        .annotate(rank=SearchRank(F("search_vector"), tsquery))
        .order_by("-rank", "id")
        .values_list("id", flat=True)[:CANDIDATES]
    )


def _to_source(chunk):
    source = Source(
        chunk_id=chunk.pk,
        source_type=chunk.source_type,
        project_id=chunk.project_id,
        project_name=chunk.project.name,
        content=chunk.content,
        task_id=chunk.task_id,
        comment_id=chunk.comment_id,
        title=chunk.project.name,
    )
    if chunk.project.is_archived:
        source.details["project"] = "archived"
    task = chunk.task
    if task is not None:
        source.title = task.title
        source.details.update(
            status=task.get_status_display(),
            priority=task.get_priority_display(),
            assignee=(task.assignee.get_full_name() or task.assignee.email)
            if task.assignee
            else "unassigned",
            due_date=task.due_date.isoformat() if task.due_date else "none",
        )
        if task.is_overdue:
            source.details["overdue"] = "yes"
    if chunk.comment is not None:
        author = chunk.comment.author
        source.details["comment_by"] = (
            (author.get_full_name() or author.email) if author else "former member"
        )
        source.details["commented_at"] = chunk.comment.created_at.date().isoformat()
    return source


def retrieve(membership, query, *, project=None, k=TOP_K, include_chunk_ids=()):
    """The best k sources for `query`, numbered 1..n, all visible to `membership`.

    `include_chunk_ids` carries sources forward from earlier turns (for
    follow-ups). They are re-checked against the visibility filter, so a source
    the user has since lost access to is silently dropped.
    """
    chunks = visible_chunks(membership, project)
    ranked = reciprocal_rank_fusion(
        _vector_ranking(chunks, query), _keyword_ranking(chunks, query)
    )
    carried = [pk for pk in include_chunk_ids if pk not in ranked[:k]]
    ids = ranked[:k] + list(chunks.filter(pk__in=carried).values_list("pk", flat=True))

    by_id = chunks.select_related(
        "project", "task__assignee", "comment__author"
    ).in_bulk(ids)
    sources = [_to_source(by_id[pk]) for pk in ids if pk in by_id]
    for number, source in enumerate(sources, start=1):
        source.index = number
    return sources
