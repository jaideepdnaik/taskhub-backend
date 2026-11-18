"""Turn text into vectors. Voyage AI in dev/prod, a deterministic fake in tests.

Pick the backend with settings.ASSISTANT_EMBEDDER ("voyage" or "fake").
Documents and queries are embedded differently (Voyage's input_type), so
always use embed_documents() for chunks and embed_query() for questions.
"""
import hashlib
import math
import re
from functools import lru_cache

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .models import EMBEDDING_DIMENSIONS

BATCH_SIZE = 64  # texts per Voyage request; well under its per-request limits


class VoyageEmbedder:
    def __init__(self):
        if not settings.VOYAGE_API_KEY:
            raise ImproperlyConfigured("Set VOYAGE_API_KEY to use Ask TaskHub.")
        import voyageai

        # Celery handles retries for indexing; the SDK retries a little for queries.
        self.client = voyageai.Client(
            api_key=settings.VOYAGE_API_KEY, max_retries=2, timeout=30
        )
        self.model = settings.ASSISTANT_EMBED_MODEL

    def _embed(self, texts, input_type):
        vectors = []
        for start in range(0, len(texts), BATCH_SIZE):
            result = self.client.embed(
                texts[start : start + BATCH_SIZE],
                model=self.model,
                input_type=input_type,
                output_dimension=EMBEDDING_DIMENSIONS,
            )
            vectors.extend(result.embeddings)
        return vectors

    def embed_documents(self, texts):
        return self._embed(list(texts), "document") if texts else []

    def embed_query(self, text):
        return self._embed([text], "query")[0]


class FakeEmbedder:
    """Hashed bag of words: texts sharing words get similar vectors.

    Deterministic and offline, so tests can check retrieval order without Voyage.
    """

    def _vector(self, text):
        vector = [0.0] * EMBEDDING_DIMENSIONS
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            digest = hashlib.sha256(word.encode()).digest()
            vector[int.from_bytes(digest[:4], "big") % EMBEDDING_DIMENSIONS] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector] if any(vector) else [1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1)

    def embed_documents(self, texts):
        return [self._vector(text) for text in texts]

    def embed_query(self, text):
        return self._vector(text)


BACKENDS = {"voyage": VoyageEmbedder, "fake": FakeEmbedder}


def embeddings_configured():
    """False when the real embedder has no key, so indexing can be skipped quietly."""
    return settings.ASSISTANT_EMBEDDER != "voyage" or bool(settings.VOYAGE_API_KEY)


@lru_cache(maxsize=4)
def _embedder_for(name):
    try:
        return BACKENDS[name]()
    except KeyError:
        raise ImproperlyConfigured(f"Unknown ASSISTANT_EMBEDDER {name!r}.") from None


def get_embedder():
    return _embedder_for(settings.ASSISTANT_EMBEDDER)
