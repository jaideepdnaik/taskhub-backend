"""Create notifications and queue their emails (after the DB commit)."""
from django.contrib.auth import get_user_model
from django.db import transaction

from .models import Notification, NotificationType

User = get_user_model()


def _user_ref(user):
    return None if user is None else {"id": user.pk, "email": user.email}


def task_payload(task, actor=None, **extra):
    """What the frontend needs to render a task notification and link to it."""
    return {
        "task_id": task.pk,
        "task_title": task.title,
        "project_id": task.project_id,
        "project_name": task.project.name,
        "actor": _user_ref(actor),
        **extra,
    }


def notify(*, recipient, organization_id, type, payload, email=False, dedupe_key=None):
    """Create an in-app notification; optionally email it once the transaction commits.

    With `dedupe_key`, a second call for the same recipient+key is a no-op
    (used by scheduled reminders so re-runs don't notify twice).
    """
    fields = {"organization_id": organization_id, "type": type, "payload": payload}
    if dedupe_key:
        notification, created = Notification.objects.get_or_create(
            recipient=recipient, dedupe_key=dedupe_key, defaults=fields
        )
        if not created:
            return notification
    else:
        notification = Notification.objects.create(recipient=recipient, **fields)

    if email:
        from .tasks import send_notification_email  # avoid an import cycle with tasks.py

        transaction.on_commit(lambda: send_notification_email.delay(notification.pk))
    return notification


def notify_task_assigned(task, actor):
    """Tell the assignee, unless they assigned themselves."""
    if task.assignee_id is None or task.assignee_id == getattr(actor, "pk", None):
        return None
    return notify(
        recipient=task.assignee,
        organization_id=task.project.organization_id,
        type=NotificationType.TASK_ASSIGNED,
        payload=task_payload(task, actor),
        email=True,
    )


def notify_comment_added(comment):
    """Tell the task's assignee and reporter (not the commenter). In-app only."""
    task = comment.task
    org_id = task.project.organization_id
    recipient_ids = {task.assignee_id, task.reporter_id} - {None, comment.author_id}
    recipients = User.objects.filter(
        pk__in=recipient_ids, memberships__organization_id=org_id  # still in the org
    )
    payload = task_payload(
        task, comment.author, comment_id=comment.pk, excerpt=comment.body[:140]
    )
    return [
        notify(
            recipient=user,
            organization_id=org_id,
            type=NotificationType.COMMENT_ADDED,
            payload=payload,
        )
        for user in recipients
    ]
