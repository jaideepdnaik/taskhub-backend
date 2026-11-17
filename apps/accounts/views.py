from django.contrib.auth import get_user_model
from django.db.models import Prefetch
from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, status
from rest_framework.response import Response

from apps.organizations.models import Membership

from .serializers import MeSerializer, RegisterResponseSerializer, RegisterSerializer

User = get_user_model()


class RegisterView(generics.GenericAPIView):
    """Create an account. Public. Returns tokens so the user is logged in right away."""

    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]
    authentication_classes = []  # a stale/invalid token shouldn't block sign-up

    @extend_schema(responses={201: RegisterResponseSerializer}, tags=["auth"])
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(RegisterResponseSerializer.for_user(user), status=status.HTTP_201_CREATED)


@extend_schema(tags=["auth"])
class MeView(generics.RetrieveUpdateAPIView):
    """The logged-in user's profile and the orgs they belong to (no X-Org-ID)."""

    serializer_class = MeSerializer
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ["get", "patch", "head", "options"]

    def get_object(self):
        memberships = Membership.objects.select_related("organization").order_by(
            "organization__name"
        )
        return User.objects.prefetch_related(
            Prefetch("memberships", queryset=memberships)
        ).get(pk=self.request.user.pk)
