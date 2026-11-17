from rest_framework.permissions import SAFE_METHODS, BasePermission


class CanEditTask(BasePermission):
    """Task write rules. Visibility (which tasks you can see at all) is the queryset's job.

    - Viewers: read only.
    - Members: create, edit and move tasks in projects they can see.
    - Delete: owner/admin, or the task's reporter.
    """

    def has_permission(self, request, view):
        membership = view.membership  # also enforces login + org membership
        if request.method in SAFE_METHODS:
            return True
        self.message = "Viewers have read-only access."
        return membership.can_edit

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        if view.action == "destroy":
            self.message = "Only admins or the task's reporter can delete it."
            return view.membership.is_admin or obj.reporter_id == request.user.id
        return True
