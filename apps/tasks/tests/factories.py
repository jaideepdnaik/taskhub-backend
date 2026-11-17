import factory

from apps.projects.tests.factories import ProjectFactory
from apps.tasks.models import Comment, Task, TaskStatus
from apps.tasks.services import next_position


class TaskFactory(factory.django.DjangoModelFactory):
    """Creates a task at the bottom of its column (like the API does)."""

    class Meta:
        model = Task

    project = factory.SubFactory(ProjectFactory)
    title = factory.Sequence(lambda n: f"Task {n}")
    status = TaskStatus.TODO
    reporter = factory.SelfAttribute("project.organization.owner")
    position = factory.LazyAttribute(lambda o: next_position(o.project.pk, o.status))


class CommentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Comment

    task = factory.SubFactory(TaskFactory)
    author = factory.SelfAttribute("task.project.organization.owner")
    body = factory.Sequence(lambda n: f"Comment {n}")
