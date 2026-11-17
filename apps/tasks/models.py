from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel
from apps.projects.models import Project


class TaskStatus(models.TextChoices):
    TODO = "todo", "To do"
    IN_PROGRESS = "in_progress", "In progress"
    REVIEW = "review", "Review"
    DONE = "done", "Done"


class TaskPriority(models.TextChoices):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"
    URGENT = "urgent", "Urgent"


# Board column order and priority order (for sorting by meaning, not alphabet).
STATUS_ORDER = [TaskStatus.TODO, TaskStatus.IN_PROGRESS, TaskStatus.REVIEW, TaskStatus.DONE]
PRIORITY_ORDER = [TaskPriority.LOW, TaskPriority.MEDIUM, TaskPriority.HIGH, TaskPriority.URGENT]


class Task(TimeStampedModel):
    """A card on a project's board."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="tasks")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    status = models.CharField(
        max_length=20, choices=TaskStatus.choices, default=TaskStatus.TODO
    )
    priority = models.CharField(
        max_length=10, choices=TaskPriority.choices, default=TaskPriority.MEDIUM
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_tasks",
    )
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="reported_tasks",
    )
    due_date = models.DateField(null=True, blank=True)
    labels = ArrayField(models.CharField(max_length=50), default=list, blank=True)
    # 0-based order within its (project, status) column.
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["project", "status", "position"]
        indexes = [
            models.Index(fields=["project", "status", "position"]),
            models.Index(fields=["assignee"]),
            models.Index(fields=["due_date"]),
            GinIndex(fields=["labels"], name="task_labels_gin"),
        ]

    def __str__(self):
        return self.title

    @property
    def is_overdue(self):
        return (
            self.due_date is not None
            and self.status != TaskStatus.DONE
            and self.due_date < timezone.localdate()
        )
