from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from apps.core.permissions import IsOrgMember
from apps.core.tenancy import ORG_HEADER_PARAMETER, OrgScopedMixin
from apps.projects.models import Project

from . import llm, services
from .models import Conversation
from .serializers import (
    AskResultSerializer,
    AskSerializer,
    ConversationDetailSerializer,
    ConversationSerializer,
    StartConversationSerializer,
)


class AssistantUnavailableError(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "The assistant is unavailable right now. Please try again shortly."
    default_code = "assistant_unavailable"


class AssistantRefusedError(APIException):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_detail = "The assistant declined to answer this question. Try rephrasing it."
    default_code = "assistant_refused"


ERROR_RESPONSES = {
    422: OpenApiResponse(description="The model declined to answer."),
    429: OpenApiResponse(description="Too many questions; see Retry-After."),
    503: OpenApiResponse(description="The model is unavailable; nothing was saved."),
}


@extend_schema_view(
    list=extend_schema(summary="My Ask TaskHub conversations in this org"),
    retrieve=extend_schema(summary="A conversation with its messages"),
    destroy=extend_schema(summary="Delete a conversation"),
)
@extend_schema(parameters=[ORG_HEADER_PARAMETER], tags=["assistant"])
class ConversationViewSet(
    OrgScopedMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Ask TaskHub: questions answered from tasks, comments and projects you can see.

    Conversations are private: only the user who started one can see it.
    Anyone else, in this org or another, gets a 404.
    """

    queryset = Conversation.objects.all()
    serializer_class = ConversationSerializer
    permission_classes = [IsOrgMember]  # asking is read-only, so viewers can ask too
    throttle_scope = "assistant"

    def get_throttles(self):
        # Only questions cost money; listing and reading aren't throttled.
        if self.action in ("create", "messages"):
            return [ScopedRateThrottle()]
        return []

    def get_queryset(self):
        queryset = super().get_queryset()
        if getattr(self, "swagger_fake_view", False):
            return queryset
        queryset = queryset.filter(user=self.request.user)
        if self.action == "retrieve":
            queryset = queryset.prefetch_related("messages")
        return queryset

    def get_serializer_class(self):
        if self.action == "retrieve":
            return ConversationDetailSerializer
        return super().get_serializer_class()

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if not getattr(self, "swagger_fake_view", False):
            context["visible_project_ids"] = set(
                Project.objects.visible_to(self.membership).values_list("pk", flat=True)
            )
        return context

    def _ask(self, conversation, question):
        try:
            return services.ask(conversation, self.membership, question)
        except llm.AssistantRefused:
            raise AssistantRefusedError()
        except llm.AssistantUnavailable:
            raise AssistantUnavailableError()

    @extend_schema(
        summary="Start a conversation with a first question",
        request=StartConversationSerializer,
        responses={201: ConversationDetailSerializer, **ERROR_RESPONSES},
    )
    def create(self, request):
        data = self._validated(StartConversationSerializer)
        project = None
        if data.get("project_id") is not None:
            project = (
                Project.objects.visible_to(self.membership).filter(pk=data["project_id"]).first()
            )
            if project is None:
                raise ValidationError({"project_id": ["Project not found."]})
        conversation = Conversation.objects.create(
            organization=self.organization,
            user=request.user,
            project=project,
            title=services.title_from(data["question"]),
        )
        self._ask(conversation, data["question"])
        serializer = ConversationDetailSerializer(conversation, context=self.get_serializer_context())
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        summary="Ask a follow-up question",
        request=AskSerializer,
        responses={201: AskResultSerializer, **ERROR_RESPONSES},
    )
    @action(detail=True, methods=["post"])
    def messages(self, request, pk=None):
        conversation = self.get_object()
        data = self._validated(AskSerializer)
        answer = self._ask(conversation, data["question"])
        question = conversation.messages.filter(role="user").latest("created_at", "id")
        serializer = AskResultSerializer(
            {"question": question, "answer": answer}, context=self.get_serializer_context()
        )
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def _validated(self, serializer_class):
        serializer = serializer_class(data=self.request.data)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data
