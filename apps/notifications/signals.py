from django.db.models.signals import post_delete
from django.dispatch import receiver

from apps.organizations.models import Membership

from .models import Notification


@receiver(post_delete, sender=Membership)
def delete_notifications_on_org_exit(sender, instance, **kwargs):
    """Left or removed from an org -> their notifications from it disappear."""
    Notification.objects.filter(
        recipient_id=instance.user_id, organization_id=instance.organization_id
    ).delete()
