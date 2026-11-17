from functools import cached_property

from django.db.models import Case, Count, IntegerField, Value, When
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.core.permissions import IsOrgMember
from apps.core.tenancy import ORG_HEADER_PARAMETER, OrgScopedMixin
from apps.projects.models import Project

from .filters import TaskFilter
from .models import PRIORITY_ORDER, STATUS_ORDER, ActivityAction, ActivityLog, Comment, Task
from .permissions import CanComment, CanEditTask
from .serializers import (
    ActivityLogSerializer,
    CommentSerializer,
    CommentWriteSerializer,
    TaskMoveSerializer,
    TaskSerializer,
    TaskWriteSerializer,
)
from .services import diff, log_activity, move_task, next_position, snapshot

ARCHIVED_READ_ONLY = "Tasks in an archived project are read-only."


def rank(field, order):
    """Sort a choice field by meaning (todo < in_progress, low < urgent), not alphabet."""
    return Case(
        *[When(**{field: value}, then=Value(i)) for i, value in enumerate(order)],
        output_field=IntegerField(),
    )


class BaseTaskViewSet(OrgScopedMixin, viewsets.GenericViewSet):
    """Shared queryset, permissions, filters and ordering for task endpoints.

    Visible tasks = tasks in projects the user can see (Project.objects.visible_to).
    Others are 404.
    """

    queryset = Task.objects.all()
    org_lookup = "project__organization"
    permission_classes = [CanEditTask]
    filterset_class = TaskFilter
    search_fields = ["title", "description"]
    ordering_fields = [
        "status_rank",
        "priority_rank",
        "position",
        "due_date",
        "created_at",
        "updated_at",
        "title",
    ]
    ordering = ["status_rank", "position", "id"]

    def get_queryset(self):
        queryset = super().get_queryset()  # filtered to the current org
        if getattr(self, "swagger_fake_view", False):
            return queryset
        return (
            queryset.visible_to(self.membership)
            .annotate(
                status_rank=rank("status", STATUS_ORDER),
                priority_rank=rank("priority", PRIORITY_ORDER),
                comment_count=Count("comments", distinct=True),
            )
            .select_related("project", "assignee", "reporter")
        )

    def write_serializer(self, project, *args, **kwargs):
        return TaskWriteSerializer(*args, context={"project": project, "request": self.request}, **kwargs)

    def task_response(self, task, status_code=status.HTTP_200_OK):
        task = self.get_queryset().get(pk=task.pk)  # fresh, with related objects
        return Response(TaskSerializer(task).data, status=status_code)

    @staticmethod
    def ensure_not_archived(project):
        if project.is_archived:
            raise ValidationError(ARCHIVED_READ_ONLY)


@extend_schema(parameters=[ORG_HEADER_PARAMETER])
class ProjectTaskViewSet(BaseTaskViewSet, mixins.ListModelMixin, mixins.CreateModelMixin):
    """A project's tasks: /api/projects/{project_pk}/tasks/ (the board)."""

    serializer_class = TaskSerializer

    @cached_property
    def project(self):
        visible = Project.objects.visible_to(self.membership)
        return get_object_or_404(visible, pk=self.kwargs["project_pk"])

    def get_queryset(self):
        queryset = super().get_queryset()
        if getattr(self, "swagger_fake_view", False):
            return queryset
        return queryset.filter(project=self.project)

    @extend_schema(request=TaskWriteSerializer, responses={201: TaskSerializer})
    def create(self, request, *args, **kwargs):
        project = self.project  # 404 if not visible
        self.ensure_not_archived(project)
        serializer = self.write_serializer(project, data=request.data)
        serializer.is_valid(raise_exception=True)
        task_status = serializer.validated_data.get("status", Task._meta.get_field("status").default)
        task = serializer.save(
            project=project,
            reporter=request.user,
            position=next_position(project.pk, task_status),
        )
        log_activity(task, request.user, ActivityAction.CREATED, {"title": task.title})
        return self.task_response(task, status.HTTP_201_CREATED)


@extend_schema(parameters=[ORG_HEADER_PARAMETER])
class TaskViewSet(
    BaseTaskViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
):
    """Tasks across every visible project: /api/tasks/ (e.g. ?assignee=me)."""

    serializer_class = TaskSerializer
    http_method_names = ["get", "patch", "delete", "head", "options"]

    @extend_schema(request=TaskWriteSerializer, responses=TaskSerializer)
    def partial_update(self, request, *args, **kwargs):
        task = self.get_object()
        self.ensure_not_archived(task.project)
        serializer = self.write_serializer(task.project, task, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        before = snapshot(task)
        new_status = serializer.validated_data.pop("status", task.status)
        serializer.save()
        if new_status != task.status:
            move_task(task, new_status)  # status change via PATCH: bottom of the new column
        changes = diff(before, snapshot(task))
        if changes:  # no-op PATCHes don't clutter the history
            log_activity(task, request.user, ActivityAction.UPDATED, changes)
        return self.task_response(task)

    def perform_destroy(self, instance):
        self.ensure_not_archived(instance.project)
        instance.delete()

    @extend_schema(
        request=TaskMoveSerializer,
        responses=TaskSerializer,
        description=(
            "Drag and drop on the board: move the task to `status` at the 0-based "
            "`position` in that column. Other cards are renumbered."
        ),
    )
    @action(detail=True, methods=["patch"])
    def move(self, request, pk=None):
        task = self.get_object()
        self.ensure_not_archived(task.project)
        serializer = TaskMoveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        before = {"status": task.status, "position": task.position}
        move_task(task, **serializer.validated_data)
        changes = diff(before, {"status": task.status, "position": task.position})
        if changes:
            log_activity(task, request.user, ActivityAction.MOVED, changes)
        return self.task_response(task)


class TaskChildMixin(OrgScopedMixin):
    """For endpoints under /api/tasks/{task_pk}/...: resolves the (visible) task."""

    org_lookup = "task__project__organization"

    @cached_property
    def task(self):
        visible = Task.objects.visible_to(self.membership).select_related("project")
        return get_object_or_404(visible, pk=self.kwargs["task_pk"])

    def get_queryset(self):
        queryset = super().get_queryset()
        if getattr(self, "swagger_fake_view", False):
            return queryset
        return queryset.filter(task=self.task)


@extend_schema(parameters=[ORG_HEADER_PARAMETER], tags=["comments"])
class CommentViewSet(
    TaskChildMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Comments on a task: /api/tasks/{task_pk}/comments/. See CanComment."""

    queryset = Comment.objects.select_related("author")
    serializer_class = CommentSerializer
    permission_classes = [CanComment]
    filter_backends = []  # plain chronological list
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    @extend_schema(request=CommentWriteSerializer, responses={201: CommentSerializer})
    def create(self, request, *args, **kwargs):
        task = self.task  # 404 if not visible
        BaseTaskViewSet.ensure_not_archived(task.project)
        serializer = CommentWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        comment = serializer.save(task=task, author=request.user)
        log_activity(task, request.user, ActivityAction.COMMENTED, {"comment_id": comment.pk})
        return Response(CommentSerializer(comment).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=CommentWriteSerializer, responses=CommentSerializer)
    def partial_update(self, request, *args, **kwargs):
        comment = self.get_object()
        BaseTaskViewSet.ensure_not_archived(self.task.project)
        serializer = CommentWriteSerializer(comment, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(edited_at=timezone.now())
        return Response(CommentSerializer(comment).data)

    def perform_destroy(self, instance):
        BaseTaskViewSet.ensure_not_archived(self.task.project)
        instance.delete()


@extend_schema(parameters=[ORG_HEADER_PARAMETER], tags=["activity"])
class ActivityLogViewSet(TaskChildMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    """A task's history, newest first: /api/tasks/{task_pk}/activity/. Read-only."""

    queryset = ActivityLog.objects.select_related("actor")
    serializer_class = ActivityLogSerializer
    permission_classes = [IsOrgMember]
    filter_backends = []
