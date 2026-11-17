from datetime import timedelta

from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsOrgMember
from apps.core.tenancy import ORG_HEADER_PARAMETER, OrgScopedMixin
from apps.organizations.models import Membership, Role
from apps.projects.models import Project
from apps.tasks.models import ActivityLog, Task, TaskPriority, TaskStatus

from .serializers import DashboardSerializer

RECENT_ACTIVITY_LIMIT = 10


class DashboardView(OrgScopedMixin, APIView):
    """Home-screen stats for the current org, limited to what the user can see.

    Task stats cover active (non-archived) projects, or just `?project=<id>`.
    """

    permission_classes = [IsOrgMember]

    @extend_schema(
        parameters=[
            ORG_HEADER_PARAMETER,
            OpenApiParameter("project", int, description="Limit stats to one visible project."),
        ],
        responses=DashboardSerializer,
        tags=["dashboard"],
    )
    def get(self, request):
        projects = Project.objects.visible_to(self.membership)
        project_param = request.query_params.get("project")
        if project_param is not None:
            if not project_param.isdigit():
                raise ValidationError({"project": ["Must be a project id."]})
            project = get_object_or_404(projects, pk=int(project_param))
            projects = projects.filter(pk=project.pk)
            tasks = Task.objects.filter(project=project)
        else:
            tasks = Task.objects.filter(project__in=projects.filter(is_archived=False))

        data = {
            "projects": projects.aggregate(
                active=Count("id", filter=Q(is_archived=False)),
                archived=Count("id", filter=Q(is_archived=True)),
            ),
            **self._task_stats(tasks, request.user),
            "members": self._member_stats(),
            "recent_activity": ActivityLog.objects.filter(task__in=tasks)
            .select_related("actor", "task")[:RECENT_ACTIVITY_LIMIT],
        }
        return Response(DashboardSerializer(data).data)

    def _task_stats(self, tasks, user):
        today = timezone.localdate()
        open_ = ~Q(status=TaskStatus.DONE)
        overdue = open_ & Q(due_date__lt=today)
        this_week = open_ & Q(due_date__gte=today, due_date__lte=today + timedelta(days=6))
        mine = Q(assignee=user)

        counts = tasks.aggregate(
            total=Count("id"),
            open=Count("id", filter=open_),
            overdue=Count("id", filter=overdue),
            due_this_week=Count("id", filter=this_week),
            unassigned=Count("id", filter=open_ & Q(assignee__isnull=True)),
            my_open=Count("id", filter=open_ & mine),
            my_overdue=Count("id", filter=overdue & mine),
            my_due_this_week=Count("id", filter=this_week & mine),
            **{f"status_{s}": Count("id", filter=Q(status=s)) for s in TaskStatus.values},
            **{f"priority_{p}": Count("id", filter=Q(priority=p)) for p in TaskPriority.values},
        )
        completed = (
            ActivityLog.objects.filter(
                task__in=tasks.filter(status=TaskStatus.DONE),
                created_at__gte=timezone.now() - timedelta(days=7),
                changes__status__to=TaskStatus.DONE,
            )
            .values("task")
            .distinct()
            .count()
        )
        return {
            "tasks": {
                "total": counts["total"],
                "open": counts["open"],
                "overdue": counts["overdue"],
                "due_this_week": counts["due_this_week"],
                "unassigned": counts["unassigned"],
                "completed_last_7_days": completed,
                "by_status": {s: counts[f"status_{s}"] for s in TaskStatus.values},
                "by_priority": {p: counts[f"priority_{p}"] for p in TaskPriority.values},
            },
            "my_tasks": {
                "open": counts["my_open"],
                "overdue": counts["my_overdue"],
                "due_this_week": counts["my_due_this_week"],
            },
        }

    def _member_stats(self):
        rows = (
            Membership.objects.filter(organization=self.organization)
            .values("role")
            .annotate(n=Count("id"))
        )
        by_role = {role: 0 for role in Role.values} | {row["role"]: row["n"] for row in rows}
        return {"total": sum(by_role.values()), "by_role": by_role}
