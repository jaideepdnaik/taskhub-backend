from rest_framework.permissions import SAFE_METHODS, BasePermission

from .models import ADMIN_ROLES, Role


class OrganizationPermission(BasePermission):
    """For /api/organizations/{id}/, which isn't scoped by X-Org-ID.

    The view's queryset only contains orgs the user belongs to (others are
    404), annotated with `my_role`. This decides what each role may do:
    read = any member, update = owner/admin, delete = owner.
    """

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        if view.action == "destroy":
            self.message = "Only the organization owner can delete it."
            return obj.my_role == Role.OWNER
        self.message = "Only organization admins can change it."
        return obj.my_role in ADMIN_ROLES


class MembershipPermission(BasePermission):
    """For /api/members/ (scoped by X-Org-ID via OrgScopedMixin).

    - Any member can list and view memberships.
    - The owner's membership can never be changed or removed here.
    - Any non-owner can remove themselves (leave the org).
    - Otherwise only owner/admin can change roles or remove members,
      and only the owner can change or remove an admin.
    """

    def has_permission(self, request, view):
        actor = view.membership  # also enforces login + org membership
        if request.method in SAFE_METHODS or view.action == "destroy":
            return True  # destroy is decided per object (leaving is allowed)
        self.message = "Only organization admins can change roles."
        return actor.is_admin

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        actor = view.membership

        if obj.role == Role.OWNER:
            self.message = "The organization owner can't be removed or have their role changed."
            return False
        if view.action == "destroy" and obj.user_id == request.user.id:
            return True  # leaving the org
        if not actor.is_admin:
            self.message = "Only organization admins can remove members."
            return False
        if obj.role == Role.ADMIN and actor.role != Role.OWNER:
            self.message = "Only the organization owner can change or remove admins."
            return False
        return True
