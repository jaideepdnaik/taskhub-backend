from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import ProjectTaskViewSet, TaskViewSet

router = DefaultRouter()
router.register("tasks", TaskViewSet, basename="task")

project_tasks = ProjectTaskViewSet.as_view({"get": "list", "post": "create"})

urlpatterns = [
    path("projects/<int:project_pk>/tasks/", project_tasks, name="project-tasks"),
    *router.urls,
]
