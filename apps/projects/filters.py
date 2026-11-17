import django_filters

from .models import Project


class ProjectFilter(django_filters.FilterSet):
    """`?is_archived=true|false` and `?member=<user id>`.

    The list hides archived projects unless `is_archived` is given (see the view).
    """

    member = django_filters.NumberFilter(field_name="members__id")

    class Meta:
        model = Project
        fields = ["is_archived", "member"]
