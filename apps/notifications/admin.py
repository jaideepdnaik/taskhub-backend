from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("recipient", "type", "organization", "is_read", "emailed_at", "created_at")
    list_filter = ("type", "is_read")
    search_fields = ("recipient__email", "organization__name")
    readonly_fields = ("payload", "dedupe_key", "emailed_at", "read_at")
    autocomplete_fields = ["recipient", "organization"]
