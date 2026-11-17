from rest_framework.routers import DefaultRouter

from .views import MembershipViewSet, OrganizationViewSet

router = DefaultRouter()
router.register("organizations", OrganizationViewSet, basename="organization")
router.register("members", MembershipViewSet, basename="member")

urlpatterns = router.urls
