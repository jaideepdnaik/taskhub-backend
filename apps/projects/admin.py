from django.contrib import admin

from .models import Project


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "is_archived", "created_by", "created_at")
    list_filter = ("is_archived",)
    search_fields = ("name", "organization__name")
    autocomplete_fields = ["organization", "created_by", "members"]
