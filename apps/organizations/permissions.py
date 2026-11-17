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
