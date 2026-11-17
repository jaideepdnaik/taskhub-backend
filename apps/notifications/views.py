from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import mixins, permissions, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Notification
from .serializers import NotificationSerializer

CountSerializer = inline_serializer("NotificationCount", {"count": serializers.IntegerField()})
UpdatedSerializer = inline_serializer("NotificationsUpdated", {"updated": serializers.IntegerField()})


@extend_schema(tags=["notifications"])
class NotificationViewSet(
    mixins.ListModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet
):
    """The logged-in user's notifications across all their orgs (no X-Org-ID).

    Only your own notifications exist here; anyone else's are 404.
    """

    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["is_read", "type", "organization"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Notification.objects.none()
        return Notification.objects.filter(recipient=self.request.user).select_related(
            "organization"
        )

    @extend_schema(responses=CountSerializer)
    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        """For the bell badge. Honours ?organization=."""
        count = self.filter_queryset(self.get_queryset()).filter(is_read=False).count()
        return Response({"count": count})

    @extend_schema(request=None, responses=NotificationSerializer)
    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        notification = self.get_object()
        if not notification.is_read:
            notification.is_read = True
            notification.read_at = timezone.now()
            notification.save(update_fields=["is_read", "read_at", "updated_at"])
        return Response(NotificationSerializer(notification).data)

    @extend_schema(request=None, responses=UpdatedSerializer)
    @action(detail=False, methods=["post"], url_path="read-all")
    def read_all(self, request):
        """Mark all (or, with ?organization=, one org's) notifications read."""
        now = timezone.now()
        updated = (
            self.filter_queryset(self.get_queryset())
            .filter(is_read=False)
            .update(is_read=True, read_at=now, updated_at=now)
        )
        return Response({"updated": updated})
