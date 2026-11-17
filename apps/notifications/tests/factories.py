import factory

from apps.notifications.models import Notification, NotificationType
from apps.organizations.tests.factories import OrganizationFactory


class NotificationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Notification

    organization = factory.SubFactory(OrganizationFactory)
    recipient = factory.SelfAttribute("organization.owner")
    type = NotificationType.TASK_ASSIGNED
    payload = factory.LazyFunction(
        lambda: {"task_id": 1, "task_title": "T", "project_id": 1, "project_name": "P", "actor": None}
    )
