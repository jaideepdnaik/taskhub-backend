from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import AcceptInvitationView, InvitationViewSet, MembershipViewSet, OrganizationViewSet

router = DefaultRouter()
router.register("organizations", OrganizationViewSet, basename="organization")
router.register("members", MembershipViewSet, basename="member")
router.register("invitations", InvitationViewSet, basename="invitation")

urlpatterns = [
    # Must come before the router, or "accept" would be read as an invitation id.
    path("invitations/accept/", AcceptInvitationView.as_view(), name="invitation-accept"),
    *router.urls,
]
