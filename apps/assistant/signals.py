"""Keep the knowledge index in sync, and clean up when someone leaves an org.

Deletes need no indexing work: chunks CASCADE with their project, task or comment.
"""
from functools import partial

from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.organizations.models import Membership
from apps.projects.models import Project
from apps.tasks.models import Comment, Task

from .embeddings import embeddings_configured
from .models import ChunkSource, Conversation

# Saves that only touch these fields can't change any chunk text.
TEXT_FIELDS = {
    ChunkSource.PROJECT: {"name", "description"},
    ChunkSource.TASK: {"title", "description", "labels"},
    ChunkSource.COMMENT: {"body"},
}


def schedule_index(kind, pk, update_fields=None):
    if update_fields is not None and not TEXT_FIELDS[kind] & set(update_fields):
        return
    if not embeddings_configured():
        return  # no VOYAGE_API_KEY yet: skip quietly; run reindex_knowledge later
    from .tasks import index_source

    transaction.on_commit(partial(index_source.delay, kind, pk))


@receiver(post_save, sender=Project)
def index_project(sender, instance, update_fields=None, **kwargs):
    schedule_index(ChunkSource.PROJECT, instance.pk, update_fields)


@receiver(post_save, sender=Task)
def index_task(sender, instance, update_fields=None, **kwargs):
    schedule_index(ChunkSource.TASK, instance.pk, update_fields)


@receiver(post_save, sender=Comment)
def index_comment(sender, instance, update_fields=None, **kwargs):
    schedule_index(ChunkSource.COMMENT, instance.pk, update_fields)


@receiver(post_delete, sender=Membership)
def delete_conversations_on_org_exit(sender, instance, **kwargs):
    """Left or removed from an org -> their Ask TaskHub threads in it are deleted."""
    Conversation.objects.filter(
        user_id=instance.user_id, organization_id=instance.organization_id
    ).delete()
