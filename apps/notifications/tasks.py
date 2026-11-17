"""Celery jobs. They take IDs (never model instances), are idempotent, and
retry with exponential back-off on mail/network errors."""
from datetime import timedelta
from smtplib import SMTPException

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from apps.organizations.models import Invitation
from apps.tasks.models import Task, TaskStatus

from .models import Notification, NotificationType
from .services import notify, task_payload

EMAIL_ERRORS = (SMTPException, ConnectionError, TimeoutError)
RETRY = {
    "autoretry_for": EMAIL_ERRORS,
    "retry_backoff": True,  # 1s, 2s, 4s, ... (with jitter)
    "retry_jitter": True,
    "max_retries": 5,
}


def build_email(notification):
    """(subject, body) for a notification email."""
    p = notification.payload
    link = f"{settings.FRONTEND_URL}/projects/{p['project_id']}/tasks/{p['task_id']}"
    actor = (p.get("actor") or {}).get("email", "Someone")

    if notification.type == NotificationType.TASK_ASSIGNED:
        subject = f"You've been assigned: {p['task_title']}"
        intro = f'{actor} assigned you to "{p["task_title"]}" in {p["project_name"]}.'
    elif notification.type == NotificationType.TASK_DUE_SOON:
        subject = f"Due {p['due_date']}: {p['task_title']}"
        intro = f'"{p["task_title"]}" in {p["project_name"]} is due on {p["due_date"]}.'
    else:
        subject = f"New comment on {p['task_title']}"
        intro = f'{actor} commented on "{p["task_title"]}":\n\n{p.get("excerpt", "")}'
    return subject, f"{intro}\n\nOpen it: {link}\n\n— TaskHub"


@shared_task(**RETRY)
def send_notification_email(notification_id):
    notification = (
        Notification.objects.select_related("recipient").filter(pk=notification_id).first()
    )
    if notification is None or notification.emailed_at is not None:
        return "skipped"  # deleted, or already sent by an earlier attempt
    subject, body = build_email(notification)
    send_mail(subject, body, None, [notification.recipient.email])
    Notification.objects.filter(pk=notification.pk, emailed_at__isnull=True).update(
        emailed_at=timezone.now()
    )
    return "sent"


@shared_task(**RETRY)
def send_invitation_email(invitation_id):
    invitation = (
        Invitation.objects.select_related("organization", "invited_by")
        .filter(pk=invitation_id)
        .first()
    )
    if invitation is None or not invitation.is_pending:
        return "skipped"  # revoked, accepted or expired meanwhile
    inviter = invitation.invited_by.email if invitation.invited_by else "Someone"
    link = f"{settings.FRONTEND_URL}/invitations/accept?token={invitation.token}"
    send_mail(
        f"You're invited to {invitation.organization.name} on TaskHub",
        (
            f"{inviter} invited you to join {invitation.organization.name} as "
            f"{invitation.get_role_display().lower()}.\n\n"
            f"Accept the invitation: {link}\n\n"
            f"This link expires on {invitation.expires_at:%Y-%m-%d}. "
            f"Sign up or log in with {invitation.email} to accept.\n\n— TaskHub"
        ),
        None,
        [invitation.email],
    )
    return "sent"


@shared_task
def send_due_date_reminders():
    """Daily (Celery Beat): remind assignees of open tasks due tomorrow.

    Safe to run more than once a day: the dedupe key stops repeat reminders.
    """
    due = timezone.localdate() + timedelta(days=1)
    tasks = (
        Task.objects.filter(due_date=due, assignee__isnull=False, project__is_archived=False)
        .exclude(status=TaskStatus.DONE)
        .select_related("project", "assignee")
    )
    created = 0
    for task in tasks:
        key = f"due_soon:{task.pk}:{due.isoformat()}"
        if Notification.objects.filter(recipient_id=task.assignee_id, dedupe_key=key).exists():
            continue  # already reminded (e.g. the job ran twice today)
        notify(  # get_or_create on the key also guards against two jobs racing
            recipient=task.assignee,
            organization_id=task.project.organization_id,
            type=NotificationType.TASK_DUE_SOON,
            payload=task_payload(task, due_date=due.isoformat()),
            email=True,
            dedupe_key=key,
        )
        created += 1
    return created
