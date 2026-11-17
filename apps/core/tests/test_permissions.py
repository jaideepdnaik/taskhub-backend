import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.views import APIView

from apps.core.permissions import (
    IsOrgAdmin,
    IsOrgAdminOrReadOnly,
    IsOrgEditorOrReadOnly,
    IsOrgMember,
    IsOrgOwner,
)
from apps.core.tenancy import OrgScopedMixin
from apps.organizations.models import Role
from apps.organizations.tests.factories import MembershipFactory, OrganizationFactory

HEADER = "HTTP_X_ORG_ID"
OWNER, ADMIN, MEMBER, VIEWER = Role.OWNER, Role.ADMIN, Role.MEMBER, Role.VIEWER


def make_view(permission):
    class View(OrgScopedMixin, APIView):
        permission_classes = [permission]

        def get(self, request):
            return Response({"ok": True})

        def post(self, request):
            return Response({"ok": True})

    return View.as_view()


def call(permission, role, method="get"):
    org = OrganizationFactory()
    user = org.owner if role == OWNER else MembershipFactory(organization=org, role=role).user
    request = getattr(APIRequestFactory(), method)("/fake/", **{HEADER: str(org.id)})
    force_authenticate(request, user=user)
    return make_view(permission)(request).status_code


# (permission, role, method) -> expected status
CASES = [
    # IsOrgMember: every role, any method
    *[(IsOrgMember, r, m, 200) for r in (OWNER, ADMIN, MEMBER, VIEWER) for m in ("get", "post")],
    # IsOrgEditorOrReadOnly: all read, viewers can't write
    *[(IsOrgEditorOrReadOnly, r, "get", 200) for r in (OWNER, ADMIN, MEMBER, VIEWER)],
    *[(IsOrgEditorOrReadOnly, r, "post", 200) for r in (OWNER, ADMIN, MEMBER)],
    (IsOrgEditorOrReadOnly, VIEWER, "post", 403),
    # IsOrgAdmin: owner/admin only, even for reads
    *[(IsOrgAdmin, r, m, 200) for r in (OWNER, ADMIN) for m in ("get", "post")],
    *[(IsOrgAdmin, r, m, 403) for r in (MEMBER, VIEWER) for m in ("get", "post")],
    # IsOrgAdminOrReadOnly: all read, owner/admin write
    *[(IsOrgAdminOrReadOnly, r, "get", 200) for r in (OWNER, ADMIN, MEMBER, VIEWER)],
    *[(IsOrgAdminOrReadOnly, r, "post", 200) for r in (OWNER, ADMIN)],
    *[(IsOrgAdminOrReadOnly, r, "post", 403) for r in (MEMBER, VIEWER)],
    # IsOrgOwner: owner only
    (IsOrgOwner, OWNER, "post", 200),
    *[(IsOrgOwner, r, "post", 403) for r in (ADMIN, MEMBER, VIEWER)],
]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "permission,role,method,expected",
    CASES,
    ids=[f"{p.__name__}-{r}-{m}" for p, r, m, _ in CASES],
)
def test_role_matrix(permission, role, method, expected):
    assert call(permission, role, method) == expected


@pytest.mark.django_db
def test_outsider_gets_404_not_403():
    org = OrganizationFactory()
    outsider = OrganizationFactory().owner
    request = APIRequestFactory().post("/fake/", **{HEADER: str(org.id)})
    force_authenticate(request, user=outsider)
    assert make_view(IsOrgOwner)(request).status_code == 404


@pytest.mark.django_db
def test_anonymous_gets_401():
    org = OrganizationFactory()
    request = APIRequestFactory().get("/fake/", **{HEADER: str(org.id)})
    assert make_view(IsOrgMember)(request).status_code == 401


def test_permission_without_mixin_is_a_config_error():
    class BadView(APIView):
        permission_classes = [IsOrgMember]

        def get(self, request):
            return Response()

    request = APIRequestFactory().get("/fake/")
    with pytest.raises(ImproperlyConfigured):
        IsOrgMember().has_permission(request, BadView())
