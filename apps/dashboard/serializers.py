"""Response shape of GET /api/dashboard/ (output only)."""
from rest_framework import serializers

from apps.tasks.serializers import ActivityLogSerializer


class ProjectCountsSerializer(serializers.Serializer):
    active = serializers.IntegerField()
    archived = serializers.IntegerField()


class ByStatusSerializer(serializers.Serializer):
    todo = serializers.IntegerField()
    in_progress = serializers.IntegerField()
    review = serializers.IntegerField()
    done = serializers.IntegerField()


class ByPrioritySerializer(serializers.Serializer):
    low = serializers.IntegerField()
    medium = serializers.IntegerField()
    high = serializers.IntegerField()
    urgent = serializers.IntegerField()


class TaskCountsSerializer(serializers.Serializer):
    total = serializers.IntegerField()
    open = serializers.IntegerField(help_text="Not done.")
    overdue = serializers.IntegerField(help_text="Open and past due.")
    due_this_week = serializers.IntegerField(help_text="Open and due in the next 7 days (incl. today).")
    unassigned = serializers.IntegerField(help_text="Open with no assignee.")
    completed_last_7_days = serializers.IntegerField(
        help_text="Tasks moved to done in the last 7 days that are still done."
    )
    by_status = ByStatusSerializer()
    by_priority = ByPrioritySerializer()


class MyTaskCountsSerializer(serializers.Serializer):
    open = serializers.IntegerField()
    overdue = serializers.IntegerField()
    due_this_week = serializers.IntegerField()


class ByRoleSerializer(serializers.Serializer):
    owner = serializers.IntegerField()
    admin = serializers.IntegerField()
    member = serializers.IntegerField()
    viewer = serializers.IntegerField()


class MemberCountsSerializer(serializers.Serializer):
    total = serializers.IntegerField()
    by_role = ByRoleSerializer()


class TaskRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    project_id = serializers.IntegerField()


class DashboardActivitySerializer(ActivityLogSerializer):
    task = TaskRefSerializer(read_only=True)

    class Meta(ActivityLogSerializer.Meta):
        fields = ActivityLogSerializer.Meta.fields + ["task"]
        read_only_fields = fields


class DashboardSerializer(serializers.Serializer):
    projects = ProjectCountsSerializer()
    tasks = TaskCountsSerializer()
    my_tasks = MyTaskCountsSerializer()
    members = MemberCountsSerializer()
    recent_activity = DashboardActivitySerializer(many=True)
