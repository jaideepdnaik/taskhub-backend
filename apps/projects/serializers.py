from rest_framework import serializers

from apps.accounts.serializers import UserSummarySerializer

from .models import Project


class ProjectSerializer(serializers.ModelSerializer):
    """List shape. `member_count` and `is_member` come from queryset annotations."""

    created_by = UserSummarySerializer(read_only=True)
    member_count = serializers.IntegerField(read_only=True)
    is_member = serializers.BooleanField(read_only=True)

    class Meta:
        model = Project
        fields = [
            "id",
            "name",
            "description",
            "is_archived",
            "created_by",
            "member_count",
            "is_member",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class ProjectDetailSerializer(ProjectSerializer):
    """Detail shape: adds the member list."""

    members = UserSummarySerializer(many=True, read_only=True)

    class Meta(ProjectSerializer.Meta):
        fields = ProjectSerializer.Meta.fields + ["members"]
        read_only_fields = fields


class ProjectWriteSerializer(serializers.ModelSerializer):
    """Create/update input. Expects `organization` in context."""

    class Meta:
        model = Project
        fields = ["name", "description"]

    def validate_name(self, value):
        name = value.strip()
        siblings = Project.objects.filter(
            organization=self.context["organization"], name__iexact=name
        )
        if self.instance is not None:
            siblings = siblings.exclude(pk=self.instance.pk)
        if siblings.exists():
            raise serializers.ValidationError("A project with this name already exists.")
        return name


class ProjectMemberAddSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()
