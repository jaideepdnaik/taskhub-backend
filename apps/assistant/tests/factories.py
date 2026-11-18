import factory

from apps.accounts.tests.factories import UserFactory
from apps.assistant.models import Conversation, Message, MessageRole
from apps.organizations.tests.factories import OrganizationFactory


class ConversationFactory(factory.django.DjangoModelFactory):
    """A conversation owned by the org's owner unless `user=` is given."""

    class Meta:
        model = Conversation

    organization = factory.SubFactory(OrganizationFactory)
    user = factory.SelfAttribute("organization.owner")
    title = factory.Sequence(lambda n: f"Question {n}")


class MessageFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Message

    conversation = factory.SubFactory(ConversationFactory)
    role = MessageRole.USER
    content = factory.Sequence(lambda n: f"Message {n}")


__all__ = ["ConversationFactory", "MessageFactory", "UserFactory"]
