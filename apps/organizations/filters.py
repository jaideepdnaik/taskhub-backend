import django_filters
from django.db.models import Q
from django.utils import timezone

from .models import Invitation, InvitationStatus


class InvitationFilter(django_filters.FilterSet):
    status = django_filters.ChoiceFilter(
        choices=InvitationStatus.choices, method="filter_status"
    )

    class Meta:
        model = Invitation
        fields = ["status", "role"]

    def filter_status(self, queryset, name, value):
        now = timezone.now()
        if value == InvitationStatus.ACCEPTED:
            return queryset.filter(accepted_at__isnull=False)
        if value == InvitationStatus.EXPIRED:
            return queryset.filter(accepted_at__isnull=True, expires_at__lte=now)
        return queryset.filter(Q(accepted_at__isnull=True) & Q(expires_at__gt=now))
