import factory

from apps.accounts.tests.factories import UserFactory
from apps.organizations.models import Membership, Organization, Role


class OrganizationFactory(factory.django.DjangoModelFactory):
    """Creates an org plus an `owner` Membership for its owner."""

    class Meta:
        model = Organization
        skip_postgeneration_save = True

    name = factory.Sequence(lambda n: f"Org {n}")
    owner = factory.SubFactory(UserFactory)

    @factory.post_generation
    def owner_membership(obj, create, extracted, **kwargs):
        if create:
            Membership.objects.create(user=obj.owner, organization=obj, role=Role.OWNER)


class MembershipFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Membership

    user = factory.SubFactory(UserFactory)
    organization = factory.SubFactory(OrganizationFactory)
    role = Role.MEMBER
