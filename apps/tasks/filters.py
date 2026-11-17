import django_filters
from django.db.models import Q
from django.utils import timezone

from .models import Task, TaskPriority, TaskStatus


def overdue_q():
    """Past its due date and not done."""
    return Q(due_date__lt=timezone.localdate()) & ~Q(status=TaskStatus.DONE)


class TaskFilter(django_filters.FilterSet):
    status = django_filters.MultipleChoiceFilter(choices=TaskStatus.choices)
    priority = django_filters.MultipleChoiceFilter(choices=TaskPriority.choices)
    assignee = django_filters.CharFilter(
        method="filter_assignee", help_text='A user id, "me", or "none" for unassigned.'
    )
    label = django_filters.CharFilter(method="filter_label", help_text="Tasks with this label.")
    due_before = django_filters.DateFilter(field_name="due_date", lookup_expr="lte")
    due_after = django_filters.DateFilter(field_name="due_date", lookup_expr="gte")
    overdue = django_filters.BooleanFilter(method="filter_overdue")
    project = django_filters.NumberFilter(field_name="project_id")

    class Meta:
        model = Task
        fields = [
            "status",
            "priority",
            "assignee",
            "label",
            "due_before",
            "due_after",
            "overdue",
            "project",
        ]

    def filter_assignee(self, queryset, name, value):
        if value == "me":
            return queryset.filter(assignee=self.request.user)
        if value == "none":
            return queryset.filter(assignee__isnull=True)
        if value.isdigit():
            return queryset.filter(assignee_id=int(value))
        return queryset.none()

    def filter_label(self, queryset, name, value):
        return queryset.filter(labels__contains=[value.strip()])

    def filter_overdue(self, queryset, name, value):
        return queryset.filter(overdue_q()) if value else queryset.exclude(overdue_q())
