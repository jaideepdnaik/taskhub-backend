from .base import *  # noqa: F401,F403

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# Ask TaskHub: never call Voyage, Anthropic or the Claude CLI from tests.
ASSISTANT_EMBEDDER = "fake"
ASSISTANT_LLM_BACKEND = "fake"
