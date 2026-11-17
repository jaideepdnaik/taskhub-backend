from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import ActivityLogViewSet, CommentViewSet, ProjectTaskViewSet, TaskViewSet

router = DefaultRouter()
router.register("tasks", TaskViewSet, basename="task")

project_tasks = ProjectTaskViewSet.as_view({"get": "list", "post": "create"})
task_comments = CommentViewSet.as_view({"get": "list", "post": "create"})
task_comment_detail = CommentViewSet.as_view(
    {"get": "retrieve", "patch": "partial_update", "delete": "destroy"}
)
task_activity = ActivityLogViewSet.as_view({"get": "list"})

urlpatterns = [
    path("projects/<int:project_pk>/tasks/", project_tasks, name="project-tasks"),
    path("tasks/<int:task_pk>/comments/", task_comments, name="task-comments"),
    path(
        "tasks/<int:task_pk>/comments/<int:pk>/",
        task_comment_detail,
        name="task-comment-detail",
    ),
    path("tasks/<int:task_pk>/activity/", task_activity, name="task-activity"),
    *router.urls,
]
