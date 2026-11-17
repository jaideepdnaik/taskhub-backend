import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from apps.core.models import TimeStampedModel

INVITATION_TTL = timedelta(days=7)


class Role(models.TextChoices):
    OWNER = "owner", "Owner"
    ADMIN = "admin", "Admin"
    MEMBER = "member", "Member"
    VIEWER = "viewer", "Viewer"


# Roles that can manage the org's projects and members.
ADMIN_ROLES = {Role.OWNER, Role.ADMIN}
# Roles that can create and edit content (everyone except viewers).
EDITOR_ROLES = {Role.OWNER, Role.ADMIN, Role.MEMBER}


class Organization(TimeStampedModel):
    """A tenant. Every project, task and member belongs to exactly one org."""

    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120, unique=True, blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,  # don't silently wipe an org when its owner is deleted
        related_name="owned_organizations",
    )
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through="Membership",
        related_name="organizations",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._unique_slug()
        super().save(*args, **kwargs)

    def _unique_slug(self):
        base = slugify(self.name)[:100] or "org"
        slug, n = base, 2
        while Organization.objects.filter(slug=slug).exists():
            slug = f"{base}-{n}"
            n += 1
        return slug


class Membership(TimeStampedModel):
    """Links a user to an org with a role."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.MEMBER)

    class Meta:
        ordering = ["organization", "user"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "organization"], name="unique_membership_per_org"
            ),
        ]

    def __str__(self):
        return f"{self.user} @ {self.organization} ({self.role})"

    @property
    def is_admin(self):
        return self.role in ADMIN_ROLES

    @property
    def can_edit(self):
        return self.role in EDITOR_ROLES


def generate_invitation_token():
    return secrets.token_urlsafe(32)


def default_invitation_expiry():
    return timezone.now() + INVITATION_TTL


class Invitation(TimeStampedModel):
    """An emailed invite to join an org. Accepting it creates a Membership."""

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="invitations"
    )
    email = models.EmailField()
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.MEMBER)
    token = models.CharField(
        max_length=64, unique=True, default=generate_invitation_token, editable=False
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="sent_invitations",
    )
    expires_at = models.DateTimeField(default=default_invitation_expiry)
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # Only one open (not yet accepted) invite per email per org.
            models.UniqueConstraint(
                fields=["organization", "email"],
                condition=models.Q(accepted_at__isnull=True),
                name="unique_pending_invitation",
            ),
            # Ownership is never handed out by invite.
            models.CheckConstraint(
                condition=~models.Q(role=Role.OWNER),
                name="invitation_role_not_owner",
            ),
        ]

    def __str__(self):
        return f"Invite {self.email} -> {self.organization} ({self.role})"

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        super().save(*args, **kwargs)

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    @property
    def is_pending(self):
        return self.accepted_at is None and not self.is_expired
