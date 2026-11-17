"""Org-level role permissions, shared by every app.

These rely on `view.membership`, which `OrgScopedMixin` provides. Reading it
also enforces login and membership (401 / 400 / 404), so these classes are
safe to use on their own without also listing `IsAuthenticated`.

Role matrix:
    viewer  - read only
    member  - read + create/edit content
    admin   - everything except deleting the org
    owner   - everything
"""
from django.core.exceptions import ImproperlyConfigured
from rest_framework.permissions import SAFE_METHODS, BasePermission

from apps.organizations.models import Role


def _membership(view):
    if not hasattr(view, "membership"):
        raise ImproperlyConfigured(
            f"{type(view).__name__} uses an org permission but not OrgScopedMixin."
        )
    return view.membership


class IsOrgMember(BasePermission):
    """Any role in the current org."""

    def has_permission(self, request, view):
        return _membership(view) is not None


class IsOrgEditorOrReadOnly(BasePermission):
    """Everyone can read; viewers can't write."""

    message = "Viewers have read-only access."

    def has_permission(self, request, view):
        membership = _membership(view)
        return request.method in SAFE_METHODS or membership.can_edit


class IsOrgAdmin(BasePermission):
    """Owner or admin only, for every method."""

    message = "Only organization admins can do this."

    def has_permission(self, request, view):
        return _membership(view).is_admin


class IsOrgAdminOrReadOnly(BasePermission):
    """Everyone can read; only owner/admin can write."""

    message = "Only organization admins can make changes."

    def has_permission(self, request, view):
        membership = _membership(view)
        return request.method in SAFE_METHODS or membership.is_admin


class IsOrgOwner(BasePermission):
    """Owner only."""

    message = "Only the organization owner can do this."

    def has_permission(self, request, view):
        return _membership(view).role == Role.OWNER
