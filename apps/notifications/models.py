from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel
from apps.organizations.models import Organization


class NotificationType(models.TextChoices):
    TASK_ASSIGNED = "task_assigned", "Task assigned"
    COMMENT_ADDED = "comment_added", "Comment added"
    TASK_DUE_SOON = "task_due_soon", "Task due soon"


class Notification(TimeStampedModel):
    """An in-app notification for one user (optionally also emailed)."""

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="notifications"
    )
    type = models.CharField(max_length=30, choices=NotificationType.choices)
    # What the frontend needs to render it, e.g. task id/title, project, actor.
    payload = models.JSONField(default=dict, blank=True)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    # Set once the email went out, so a retried Celery job never sends twice.
    emailed_at = models.DateTimeField(null=True, blank=True)
    # Optional idempotency key, e.g. "due_soon:<task>:<date>" for scheduled reminders.
    dedupe_key = models.CharField(max_length=100, null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["recipient", "is_read", "-created_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["recipient", "dedupe_key"],
                condition=models.Q(dedupe_key__isnull=False),
                name="unique_notification_dedupe_key",
            ),
        ]

    def __str__(self):
        return f"{self.type} -> {self.recipient}"
