import pytest
from django.contrib.auth import get_user_model

User = get_user_model()


@pytest.mark.django_db
def test_create_user_uses_email():
    user = User.objects.create_user(email="Jane@Example.com", password="s3cret-pass!")
    assert user.email == "Jane@example.com"
    assert user.check_password("s3cret-pass!")
    assert not user.is_staff


@pytest.mark.django_db
def test_jwt_login_returns_tokens(api_client):
    User.objects.create_user(email="jane@example.com", password="s3cret-pass!")
    response = api_client.post(
        "/api/auth/login/", {"email": "jane@example.com", "password": "s3cret-pass!"}
    )
    assert response.status_code == 200
    assert {"access", "refresh"} <= response.json().keys()
