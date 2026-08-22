from functools import lru_cache

from app.config import settings
from app.rag.embedding.base import EmbeddingProvider
from app.rag.embedding.deterministic import DeterministicEmbeddingProvider
from app.rag.embedding.huggingface import HuggingFaceEmbeddingProvider
from app.rag.embedding.openai import OpenAIEmbeddingProvider


@lru_cache(maxsize=1)
def get_embedding_provider() -> EmbeddingProvider:
    backend = settings.EMBEDDING_BACKEND.strip().lower()
    common = {
        "model_name": settings.EMBEDDING_MODEL_NAME,
        "version": settings.EMBEDDING_VERSION,
        "dimension": settings.EMBEDDING_DIMENSION,
        "batch_size": settings.EMBEDDING_BATCH_SIZE,
    }
    if backend in {"sentence_transformers", "huggingface"}:
        return HuggingFaceEmbeddingProvider(
            **common,
            device=settings.EMBEDDING_DEVICE,
            dtype=settings.EMBEDDING_DTYPE,
            cache_folder=settings.EMBEDDING_CACHE_FOLDER,
            local_files_only=settings.EMBEDDING_LOCAL_FILES_ONLY,
            model_path=settings.EMBEDDING_MODEL_PATH,
        )
    if backend == "openai":
        return OpenAIEmbeddingProvider(
            **common,
            api_key=settings.OPENAI_API_KEY or "",
        )
    return DeterministicEmbeddingProvider(
        dimension=settings.EMBEDDING_DIMENSION,
        version=settings.EMBEDDING_VERSION,
        batch_size=settings.EMBEDDING_BATCH_SIZE,
    )
