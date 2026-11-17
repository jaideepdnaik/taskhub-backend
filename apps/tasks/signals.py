"""Unassign tasks when their assignee loses access to the project."""
from django.db.models.signals import m2m_changed, post_delete
from django.dispatch import receiver

from apps.organizations.models import Membership
from apps.projects.models import Project

from .models import Task


@receiver(post_delete, sender=Membership)
def unassign_on_org_exit(sender, instance, **kwargs):
    """Left or removed from the org -> unassigned from all its tasks."""
    Task.objects.filter(
        project__organization_id=instance.organization_id, assignee_id=instance.user_id
    ).update(assignee=None)


@receiver(m2m_changed, sender=Project.members.through)
def unassign_on_project_exit(sender, instance, action, reverse, pk_set, **kwargs):
    """Removed from a project -> unassigned from that project's tasks."""
    if action not in ("post_remove", "post_clear"):
        return
    tasks = Task.objects.all()
    if not reverse:  # project.members.remove(users) / .clear()
        tasks = tasks.filter(project=instance)
        if action == "post_remove":
            tasks = tasks.filter(assignee_id__in=pk_set)
    else:  # user.projects.remove(projects) / .clear()
        tasks = tasks.filter(assignee=instance)
        if action == "post_remove":
            tasks = tasks.filter(project_id__in=pk_set)
    tasks.exclude(assignee__isnull=True).update(assignee=None)
