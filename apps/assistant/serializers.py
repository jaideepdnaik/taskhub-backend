from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import ChunkSource, Conversation, Message

QUESTION_MAX_LENGTH = 2000


class AskSerializer(serializers.Serializer):
    """Input for starting a conversation or asking a follow-up."""

    question = serializers.CharField(max_length=QUESTION_MAX_LENGTH, trim_whitespace=True)


class StartConversationSerializer(AskSerializer):
    project_id = serializers.IntegerField(
        required=False,
        allow_null=True,
        help_text="Limit answers to one project you can see.",
    )


class CitationSerializer(serializers.Serializer):
    """A source the answer cited as [index]. Link it with task_id / comment_id."""

    index = serializers.IntegerField()
    source_type = serializers.ChoiceField(choices=ChunkSource.choices)
    project_id = serializers.IntegerField()
    task_id = serializers.IntegerField(allow_null=True)
    comment_id = serializers.IntegerField(allow_null=True)
    title = serializers.CharField()
    excerpt = serializers.CharField()


class MessageSerializer(serializers.ModelSerializer):
    citations = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = ["id", "role", "content", "citations", "created_at"]
        read_only_fields = fields

    @extend_schema_field(CitationSerializer(many=True))
    def get_citations(self, message):
        # Hide citations to projects the user can no longer see (context from the view).
        visible = self.context.get("visible_project_ids")
        citations = [
            c for c in message.citations if visible is None or c["project_id"] in visible
        ]
        return CitationSerializer(citations, many=True).data


class ConversationSerializer(serializers.ModelSerializer):
    project_id = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = Conversation
        fields = ["id", "title", "project_id", "created_at", "updated_at"]
        read_only_fields = fields


class ConversationDetailSerializer(ConversationSerializer):
    messages = MessageSerializer(many=True, read_only=True)

    class Meta(ConversationSerializer.Meta):
        fields = [*ConversationSerializer.Meta.fields, "messages"]
        read_only_fields = fields


class AskResultSerializer(serializers.Serializer):
    """A follow-up's result: the saved question and the answer."""

    question = MessageSerializer()
    answer = MessageSerializer()
