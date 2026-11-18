"""Celery jobs for Ask TaskHub. They take IDs, are idempotent and retry on network errors."""
from celery import shared_task
from voyageai import error as voyage_error

from . import indexing

EMBEDDING_ERRORS = (
    voyage_error.RateLimitError,
    voyage_error.ServiceUnavailableError,
    voyage_error.ServerError,
    voyage_error.APIConnectionError,
    voyage_error.Timeout,
    ConnectionError,
    TimeoutError,
)


@shared_task(
    autoretry_for=EMBEDDING_ERRORS, retry_backoff=True, retry_jitter=True, max_retries=5
)
def index_source(kind, pk):
    """Refresh the knowledge chunks of one project, task or comment."""
    return indexing.index_source(kind, pk)
