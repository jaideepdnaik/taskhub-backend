from django.contrib.auth import get_user_model
from django.db.models import Count, Exists, OuterRef
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.accounts.serializers import UserSummarySerializer
from apps.core.permissions import IsOrgAdminOrReadOnly
from apps.core.tenancy import ORG_HEADER_PARAMETER, OrgScopedMixin

from .filters import ProjectFilter
from .models import Project
from .serializers import (
    ProjectDetailSerializer,
    ProjectMemberAddSerializer,
    ProjectSerializer,
    ProjectWriteSerializer,
)

User = get_user_model()
ARCHIVED_READ_ONLY = "Archived projects are read-only. Unarchive it first."


@extend_schema(parameters=[ORG_HEADER_PARAMETER])
class ProjectViewSet(OrgScopedMixin, viewsets.ModelViewSet):
    """Projects in the current org (X-Org-ID).

    Owner/admin see and manage every project. Members and viewers only see
    projects they belong to (others are 404) and can't change them.
    """

    queryset = Project.objects.all()
    permission_classes = [IsOrgAdminOrReadOnly]
    filterset_class = ProjectFilter
    search_fields = ["name", "description"]
    ordering_fields = ["name", "created_at", "updated_at"]
    ordering = ["name"]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        queryset = super().get_queryset()  # already filtered to the current org
        if getattr(self, "swagger_fake_view", False):
            return queryset
        is_member = Exists(
            Project.members.through.objects.filter(
                project_id=OuterRef("pk"), user_id=self.request.user.pk
            )
        )
        # Note: use the Exists subquery (not filter(members=user)) for visibility,
        # otherwise the join is shared with Count("members") and the count shrinks to 1.
        queryset = queryset.annotate(
            member_count=Count("members", distinct=True),
            is_member=is_member,
        ).select_related("created_by")
        if not self.membership.is_admin:
            queryset = queryset.filter(is_member=True)
        if self.action == "list" and "is_archived" not in self.request.query_params:
            queryset = queryset.filter(is_archived=False)
        if self.action != "list":
            queryset = queryset.prefetch_related("members")
        return queryset

    def get_serializer_class(self):
        if self.action in ("create", "partial_update"):
            return ProjectWriteSerializer
        if self.action == "list":
            return ProjectSerializer
        return ProjectDetailSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if not getattr(self, "swagger_fake_view", False):
            context["organization"] = self.organization
        return context

    def _detail_response(self, pk, status_code=status.HTTP_200_OK):
        """Re-read the project through get_queryset so annotations are present."""
        project = self.get_queryset().get(pk=pk)
        return Response(ProjectDetailSerializer(project).data, status=status_code)

    @extend_schema(request=ProjectWriteSerializer, responses={201: ProjectDetailSerializer})
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        project = serializer.save(organization=self.organization, created_by=request.user)
        project.members.add(request.user)
        return self._detail_response(project.pk, status.HTTP_201_CREATED)

    @extend_schema(request=ProjectWriteSerializer, responses=ProjectDetailSerializer)
    def partial_update(self, request, *args, **kwargs):
        project = self.get_object()
        if project.is_archived:
            raise ValidationError(ARCHIVED_READ_ONLY)
        serializer = self.get_serializer(project, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return self._detail_response(project.pk)

    # --- archive -----------------------------------------------------------

    def _set_archived(self, value):
        project = self.get_object()
        if project.is_archived != value:
            project.is_archived = value
            project.save(update_fields=["is_archived", "updated_at"])
        return self._detail_response(project.pk)

    @extend_schema(request=None, responses=ProjectDetailSerializer)
    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        return self._set_archived(True)

    @extend_schema(request=None, responses=ProjectDetailSerializer)
    @action(detail=True, methods=["post"])
    def unarchive(self, request, pk=None):
        return self._set_archived(False)

    # --- members -----------------------------------------------------------

    @extend_schema(
        methods=["get"], request=None, responses=UserSummarySerializer(many=True)
    )
    @extend_schema(
        methods=["post"],
        request=ProjectMemberAddSerializer,
        responses=UserSummarySerializer(many=True),
        description="Add an org member to the project. Returns the updated member list.",
    )
    @action(detail=True, methods=["get", "post"], pagination_class=None)
    def members(self, request, pk=None):
        project = self.get_object()
        if request.method == "POST":
            if project.is_archived:
                raise ValidationError(ARCHIVED_READ_ONLY)
            serializer = ProjectMemberAddSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            user = User.objects.filter(
                pk=serializer.validated_data["user_id"],
                memberships__organization=self.organization,
            ).first()
            if user is None:
                raise ValidationError({"user_id": ["This user isn't a member of the organization."]})
            project.members.add(user)  # no-op if already a member
        members = project.members.order_by("email")
        return Response(UserSummarySerializer(members, many=True).data)

    @extend_schema(
        request=None,
        responses={204: None},
        parameters=[OpenApiParameter("user_id", int, OpenApiParameter.PATH)],
    )
    @action(detail=True, methods=["delete"], url_path=r"members/(?P<user_id>\d+)")
    def remove_project_member(self, request, pk=None, user_id=None):
        project = self.get_object()
        if project.is_archived:
            raise ValidationError(ARCHIVED_READ_ONLY)
        project.members.remove(int(user_id))  # no-op if not a member
        return Response(status=status.HTTP_204_NO_CONTENT)
