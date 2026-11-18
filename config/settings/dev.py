from .base import *  # noqa: F401,F403

DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]
CORS_ALLOWED_ORIGINS = ["http://localhost:5173"]

# Ask TaskHub: answer with your logged-in Claude Code CLI unless .env says otherwise.
ASSISTANT_LLM_BACKEND = env("ASSISTANT_LLM_BACKEND", default="cli")  # noqa: F405
