from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken

from apps.organizations.models import Membership, Role

User = get_user_model()


class UserSummarySerializer(serializers.ModelSerializer):
    """Compact, read-only user shape for nesting (owner, assignee, author...)."""

    full_name = serializers.CharField(source="get_full_name", read_only=True)

    class Meta:
        model = User
        fields = ["id", "email", "full_name"]
        read_only_fields = fields


class RegisterSerializer(serializers.ModelSerializer):
    """Sign-up input. Email is stored lowercase and must be unique ignoring case."""

    password = serializers.CharField(write_only=True, style={"input_type": "password"})

    class Meta:
        model = User
        fields = ["email", "password", "first_name", "last_name"]
        extra_kwargs = {
            # Uniqueness is checked case-insensitively in validate_email instead.
            "email": {"validators": []},
            "first_name": {"required": False},
            "last_name": {"required": False},
        }

    def validate_email(self, value):
        email = value.strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return email

    def validate(self, attrs):
        # Run Django's AUTH_PASSWORD_VALIDATORS, including "too similar to email/name".
        candidate = User(
            email=attrs["email"],
            first_name=attrs.get("first_name", ""),
            last_name=attrs.get("last_name", ""),
        )
        try:
            validate_password(attrs["password"], user=candidate)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)})
        return attrs

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)


class MyOrganizationSerializer(serializers.ModelSerializer):
    """One entry in /me/'s org list: the org plus my role in it."""

    id = serializers.IntegerField(source="organization.id", read_only=True)
    name = serializers.CharField(source="organization.name", read_only=True)
    slug = serializers.CharField(source="organization.slug", read_only=True)
    role = serializers.ChoiceField(choices=Role.choices, read_only=True)

    class Meta:
        model = Membership
        fields = ["id", "name", "slug", "role"]
        read_only_fields = fields


class MeSerializer(serializers.ModelSerializer):
    """The logged-in user's profile. Only the names are editable."""

    full_name = serializers.CharField(source="get_full_name", read_only=True)
    organizations = MyOrganizationSerializer(source="memberships", many=True, read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "first_name",
            "last_name",
            "full_name",
            "date_joined",
            "organizations",
        ]
        read_only_fields = ["id", "email", "date_joined"]


class RegisterResponseSerializer(serializers.Serializer):
    """Registration response: the new profile plus JWT tokens (already logged in)."""

    user = MeSerializer()
    access = serializers.CharField()
    refresh = serializers.CharField()

    @staticmethod
    def for_user(user):
        refresh = RefreshToken.for_user(user)
        return RegisterResponseSerializer(
            {"user": user, "access": str(refresh.access_token), "refresh": str(refresh)}
        ).data
