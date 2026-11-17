from django.contrib import admin

from .models import ActivityLog, Comment, Task


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("title", "project", "status", "priority", "assignee", "due_date")
    list_filter = ("status", "priority")
    search_fields = ("title", "project__name", "assignee__email")
    autocomplete_fields = ["project", "assignee", "reporter"]


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ("task", "author", "created_at", "edited_at")
    search_fields = ("body", "task__title", "author__email")
    autocomplete_fields = ["task", "author"]


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ("task", "actor", "action", "created_at")
    list_filter = ("action",)
    search_fields = ("task__title", "actor__email")
    readonly_fields = ("task", "actor", "action", "changes", "created_at", "updated_at")
