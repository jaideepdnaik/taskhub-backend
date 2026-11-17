from rest_framework import serializers

from apps.accounts.serializers import UserSummarySerializer
from apps.projects.models import Project

from .models import ActivityAction, ActivityLog, Comment, Task, TaskStatus

MAX_LABELS = 20


class ProjectSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = ["id", "name"]
        read_only_fields = fields


class TaskSerializer(serializers.ModelSerializer):
    """Read shape for a task."""

    project = ProjectSummarySerializer(read_only=True)
    assignee = UserSummarySerializer(read_only=True)
    reporter = UserSummarySerializer(read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)
    comment_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Task
        fields = [
            "id",
            "project",
            "title",
            "description",
            "status",
            "priority",
            "assignee",
            "reporter",
            "due_date",
            "is_overdue",
            "labels",
            "position",
            "comment_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class TaskWriteSerializer(serializers.ModelSerializer):
    """Create/update input. Expects `project` (the task's project) in context.

    `assignee_id` must be a member of that project (or null to unassign).
    `position` isn't writable here; use the move action.
    """

    assignee_id = serializers.IntegerField(required=False, allow_null=True)
    labels = serializers.ListField(
        # Blanks are allowed here and dropped in validate_labels.
        child=serializers.CharField(max_length=50, allow_blank=True),
        required=False,
        max_length=MAX_LABELS,
    )

    class Meta:
        model = Task
        fields = ["title", "description", "status", "priority", "assignee_id", "due_date", "labels"]

    def validate_title(self, value):
        title = value.strip()
        if not title:
            raise serializers.ValidationError("This field may not be blank.")
        return title

    def validate_labels(self, value):
        # Trim, drop blanks and duplicates, keep the given order.
        cleaned = []
        for label in value:
            label = label.strip()
            if label and label not in cleaned:
                cleaned.append(label)
        return cleaned

    def validate_assignee_id(self, value):
        if value is None:
            return None
        project = self.context["project"]
        if not project.members.filter(pk=value).exists():
            raise serializers.ValidationError("The assignee must be a member of this project.")
        return value


class TaskMoveSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=TaskStatus.choices)
    position = serializers.IntegerField(
        min_value=0, help_text="0-based slot in the target column; clamped to its end."
    )


class CommentSerializer(serializers.ModelSerializer):
    """Read shape for a comment."""

    author = UserSummarySerializer(read_only=True)

    class Meta:
        model = Comment
        fields = ["id", "author", "body", "edited_at", "created_at"]
        read_only_fields = fields


class CommentWriteSerializer(serializers.ModelSerializer):
    """Create/edit input: just the text (trimmed, not blank, max 5000 chars)."""

    body = serializers.CharField(max_length=5000)

    class Meta:
        model = Comment
        fields = ["body"]


class ActivityLogSerializer(serializers.ModelSerializer):
    actor = UserSummarySerializer(read_only=True)
    action = serializers.ChoiceField(choices=ActivityAction.choices, read_only=True)
    changes = serializers.DictField(
        read_only=True,
        help_text='e.g. {"priority": {"from": "medium", "to": "urgent"}}',
    )

    class Meta:
        model = ActivityLog
        fields = ["id", "actor", "action", "changes", "created_at"]
        read_only_fields = fields
