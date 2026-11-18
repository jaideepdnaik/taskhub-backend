from django.contrib import admin

from .models import Conversation, KnowledgeChunk, Message


@admin.register(KnowledgeChunk)
class KnowledgeChunkAdmin(admin.ModelAdmin):
    list_display = ("source_key", "chunk_index", "organization", "project", "updated_at")
    list_filter = ("source_type",)
    search_fields = ("source_key", "content")
    exclude = ("embedding",)
    readonly_fields = ("content_hash",)
    raw_id_fields = ("organization", "project", "task", "comment")


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    fields = ("role", "content", "model", "created_at")
    readonly_fields = fields


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "organization", "project", "updated_at")
    search_fields = ("title", "user__email", "organization__name")
    raw_id_fields = ("organization", "user", "project")
    inlines = [MessageInline]
