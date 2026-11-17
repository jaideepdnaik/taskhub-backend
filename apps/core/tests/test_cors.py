import pytest

ORIGIN = "http://localhost:5173"


@pytest.mark.django_db
def test_preflight_allows_org_header(client, settings):
    """Browsers preflight custom headers; X-Org-ID must be allowed or every org call fails."""
    settings.CORS_ALLOWED_ORIGINS = [ORIGIN]
    response = client.options(
        "/api/projects/",
        HTTP_ORIGIN=ORIGIN,
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="authorization, x-org-id, content-type",
    )
    assert response.status_code == 200
    assert response["access-control-allow-origin"] == ORIGIN
    allowed = {h.strip() for h in response["access-control-allow-headers"].split(",")}
    assert {"authorization", "x-org-id", "content-type"} <= allowed
