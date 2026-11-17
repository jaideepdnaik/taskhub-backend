import factory

from apps.organizations.tests.factories import OrganizationFactory
from apps.projects.models import Project


class ProjectFactory(factory.django.DjangoModelFactory):
    """Creates a project; pass `members=[user, ...]` to add members."""

    class Meta:
        model = Project
        skip_postgeneration_save = True

    organization = factory.SubFactory(OrganizationFactory)
    name = factory.Sequence(lambda n: f"Project {n}")
    created_by = factory.SelfAttribute("organization.owner")

    @factory.post_generation
    def members(obj, create, extracted, **kwargs):
        if create and extracted:
            obj.members.add(*extracted)
