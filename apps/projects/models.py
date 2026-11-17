from django.conf import settings
from django.db import models
from django.db.models.functions import Lower

from apps.core.models import TimeStampedModel
from apps.organizations.models import Organization


class ProjectQuerySet(models.QuerySet):
    def member_exists(self, user):
        """An Exists() expression: is `user` a member of the outer project?"""
        return models.Exists(
            Project.members.through.objects.filter(
                project_id=models.OuterRef("pk"), user_id=user.pk
            )
        )

    def visible_to(self, membership):
        """Projects in membership's org that this user may see.

        Owner/admin see all of them; members/viewers only those they belong to.
        Uses Exists (not filter(members=...)) so later Count("members") stays correct.
        """
        queryset = self.filter(organization_id=membership.organization_id)
        if membership.is_admin:
            return queryset
        return queryset.filter(self.member_exists(membership.user))


class Project(TimeStampedModel):
    """A container for tasks inside an organization."""

    objects = ProjectQuerySet.as_manager()

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="projects"
    )
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name="projects", blank=True
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_projects",
    )
    is_archived = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]
        constraints = [
            # One "Website" per org, ignoring case.
            models.UniqueConstraint(
                Lower("name"), "organization", name="unique_project_name_per_org"
            ),
        ]
        indexes = [models.Index(fields=["organization", "is_archived"])]

    def __str__(self):
        return self.name
