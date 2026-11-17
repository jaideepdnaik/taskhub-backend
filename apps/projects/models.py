from django.conf import settings
from django.db import models
from django.db.models.functions import Lower

from apps.core.models import TimeStampedModel
from apps.organizations.models import Organization


class Project(TimeStampedModel):
    """A container for tasks inside an organization."""

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
