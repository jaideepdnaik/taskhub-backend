from django.db.models.signals import post_delete
from django.dispatch import receiver

from apps.organizations.models import Membership

from .models import Project


@receiver(post_delete, sender=Membership)
def remove_departed_user_from_projects(sender, instance, **kwargs):
    """When someone leaves or is removed from an org, drop them from its projects."""
    Project.members.through.objects.filter(
        project__organization_id=instance.organization_id, user_id=instance.user_id
    ).delete()
