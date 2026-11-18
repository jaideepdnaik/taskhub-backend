"""Build and refresh KnowledgeChunks for projects, tasks and comments.

index_source(kind, pk) is idempotent: it re-reads the object, re-chunks it,
embeds only chunks whose text changed (by content hash) and removes chunks
that no longer exist. A deleted source simply ends up with no chunks.
"""
import hashlib

from django.db import transaction

from apps.projects.models import Project
from apps.tasks.models import Comment, Task

from .embeddings import get_embedder
from .models import ChunkSource, KnowledgeChunk

CHUNK_SIZE = 1500  # characters, roughly 300-400 tokens
CHUNK_OVERLAP = 200


def split_text(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """Split long text into overlapping windows, breaking at whitespace."""
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    chunks, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            space = text.rfind(" ", start + size // 2, end)
            end = space if space != -1 else end
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [chunk for chunk in chunks if chunk]


def _with_header(header, body):
    """Prefix every chunk with its source's header so each chunk stands alone."""
    parts = split_text(body) or [""]
    return [f"{header}\n\n{part}".strip() for part in parts]


def project_texts(project):
    return _with_header(f"Project: {project.name}", project.description)


def task_texts(task):
    header = f"Task: {task.title}"
    if task.labels:
        header += f"\nLabels: {', '.join(task.labels)}"
    return _with_header(header, task.description)


def comment_texts(comment):
    return _with_header(f"Comment on task: {comment.task.title}", comment.body)


def _load(kind, pk):
    """(source, texts, fk fields) or (None, [], {}) if the source is gone."""
    if kind == ChunkSource.PROJECT:
        project = Project.objects.filter(pk=pk).first()
        if project:
            return project, project_texts(project), {"project": project}
    elif kind == ChunkSource.TASK:
        task = Task.objects.select_related("project").filter(pk=pk).first()
        if task:
            return task, task_texts(task), {"project": task.project, "task": task}
    elif kind == ChunkSource.COMMENT:
        comment = Comment.objects.select_related("task__project").filter(pk=pk).first()
        if comment:
            fields = {"project": comment.task.project, "task": comment.task, "comment": comment}
            return comment, comment_texts(comment), fields
    else:
        raise ValueError(f"Unknown source kind {kind!r}")
    return None, [], {}


def _hash(text):
    return hashlib.sha256(text.encode()).hexdigest()


def index_source(kind, pk):
    """Bring one source's chunks up to date. Returns "indexed", "unchanged" or "deleted"."""
    source_key = f"{kind}:{pk}"
    source, texts, fields = _load(kind, pk)
    if source is None:
        KnowledgeChunk.objects.filter(source_key=source_key).delete()
        return "deleted"

    existing = {c.chunk_index: c for c in KnowledgeChunk.objects.filter(source_key=source_key)}
    hashes = [_hash(text) for text in texts]
    changed = [
        i for i, h in enumerate(hashes) if i not in existing or existing[i].content_hash != h
    ]
    stale = [i for i in existing if i >= len(texts)]
    if not changed and not stale:
        result = "unchanged"
    else:
        # Embed outside the transaction: it's a network call.
        vectors = get_embedder().embed_documents([texts[i] for i in changed])
        with transaction.atomic():
            KnowledgeChunk.objects.filter(source_key=source_key, chunk_index__in=stale).delete()
            for i, vector in zip(changed, vectors):
                KnowledgeChunk.objects.update_or_create(
                    source_key=source_key,
                    chunk_index=i,
                    defaults={
                        "organization_id": fields["project"].organization_id,
                        "source_type": kind,
                        "content": texts[i],
                        "content_hash": hashes[i],
                        "embedding": vector,
                        **fields,
                    },
                )
        result = "indexed"

    if kind == ChunkSource.TASK:
        # Comment chunks include the task title; re-check them (cheap when unchanged).
        for comment_id in source.comments.values_list("pk", flat=True):
            index_source(ChunkSource.COMMENT, comment_id)
    return result


def reindex(organization_id=None):
    """Index everything (optionally one org). Returns {kind: count}."""
    projects = Project.objects.all()
    tasks = Task.objects.all()
    if organization_id is not None:
        projects = projects.filter(organization_id=organization_id)
        tasks = tasks.filter(project__organization_id=organization_id)
    counts = {"project": 0, "task": 0}
    for pk in projects.values_list("pk", flat=True):
        index_source(ChunkSource.PROJECT, pk)
        counts["project"] += 1
    for pk in tasks.values_list("pk", flat=True):
        index_source(ChunkSource.TASK, pk)  # also covers the task's comments
        counts["task"] += 1
    return counts
