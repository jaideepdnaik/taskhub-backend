from django.apps import AppConfig


class AssistantConfig(AppConfig):
    name = "apps.assistant"
    verbose_name = "Ask TaskHub"

    def ready(self):
        from . import signals  # noqa: F401  (registers signal receivers)
