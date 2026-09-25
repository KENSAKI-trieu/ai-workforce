"""Versioned, batch-oriented embedding service for the knowledge pipeline."""

import hashlib
import logging
import math
import os
import re
import time
from functools import lru_cache
from pathlib import Path
from collections.abc import Callable
from typing import Any

from app.core.config import settings
from app.clients.ai_service_client import get_ai_service_client

_TOKEN_PATTERN = re.compile(r"\S+")
logger = logging.getLogger(__name__)


def is_embedding_resource_error(error: BaseException) -> bool:
    """Identify model-load failures that are safe to degrade to local hash vectors."""
    message = str(error).lower()
    return isinstance(error, MemoryError) or any(fragment in message for fragment in (
        "paging file is too small",
        "os error 1455",
        "out of memory",
        "cannot allocate memory",
        "can't allocate memory",
        "not enough memory",
    ))


def is_embedding_model_unavailable_error(error: BaseException) -> bool:
    """Allow the configured fallback when weights are unavailable offline."""
    message = str(error).lower()
    return is_embedding_resource_error(error) or any(fragment in message for fragment in (
        "cannot find the requested files in the disk cache",
        "outgoing traffic has been disabled",
        "local_files_only",
    ))


def normalize_embedding_device(value: str) -> str:
    """Map user-facing GPU aliases to device names understood by PyTorch."""
    normalized = value.strip().lower()
    if normalized in {"gpu", "nvidia"}:
        return "cuda"
    return normalized


def calculate_content_hash(text: str) -> str:
    normalized = " ".join(text.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def build_embedding_text(chunk: dict[str, Any]) -> str:
    parts = [
        f"Phòng ban: {chunk.get('department', '')}",
        f"Loại tài liệu: {chunk.get('document_type', '')}",
        f"Tên tài liệu: {chunk.get('document_title', '')}",
        f"Mục: {chunk.get('section_title', '')}",
        "",
        "Nội dung:",
        chunk["content"],
    ]
    return "\n".join(parts).strip()


class EmbeddingService:
    def __init__(self) -> None:
        self.backend = settings.EMBEDDING_BACKEND.strip().lower()
        self.configured_model_name = settings.EMBEDDING_MODEL_NAME
        self.dimension = settings.EMBEDDING_DIMENSION
        self.batch_size = settings.EMBEDDING_BATCH_SIZE
        self.max_retries = settings.EMBEDDING_MAX_RETRIES
        self.configured_version = settings.EMBEDDING_VERSION
        self.device = normalize_embedding_device(settings.EMBEDDING_DEVICE)
        self._model = None
        self._tokenizer = None
        self._tokenizer_load_attempted = False
        self._remote_max_input_tokens: int | None = None

    @property
    def model_name(self) -> str:
        if get_ai_service_client().enabled or self.backend in {"sentence_transformers", "gemini"}:
            return self.configured_model_name
        return f"deterministic-hash-{self.dimension}"

    @property
    def version(self) -> str:
        if get_ai_service_client().enabled or self.backend in {"sentence_transformers", "gemini"}:
            return self.configured_version
        return f"deterministic-hash-{self.dimension}-v1"

    @property
    def max_input_tokens(self) -> int:
        ai_client = get_ai_service_client()
        if ai_client.enabled:
            if self._remote_max_input_tokens is None:
                self._remote_max_input_tokens = int(
                    ai_client.count_tokens([""])["max_input_tokens"]
                )
            return self._remote_max_input_tokens
        tokenizer = self._load_tokenizer()
        if tokenizer is None:
            return 8192
        model_max_length = int(getattr(tokenizer, "model_max_length", 8192))
        return model_max_length if 0 < model_max_length < 10_000_000 else 8192

    def _load_tokenizer(self):
        if get_ai_service_client().enabled or self.backend != "sentence_transformers":
            return None
        if self._tokenizer is not None or self._tokenizer_load_attempted:
            return self._tokenizer
        self._tokenizer_load_attempted = True
        try:
            from transformers import AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(
                self.configured_model_name,
                cache_dir=settings.EMBEDDING_CACHE_FOLDER,
                local_files_only=settings.EMBEDDING_LOCAL_FILES_ONLY,
            )
        except Exception as exc:
            logger.warning(
                "Embedding tokenizer %s could not be loaded; lexical token counts will be used: %s",
                self.configured_model_name,
                exc,
            )
            self._tokenizer = None
        return self._tokenizer

    def _load_model(self):
        if get_ai_service_client().enabled:
            return None
        if self._model is not None:
            return self._model
        if self.backend != "sentence_transformers":
            return None
        if settings.EMBEDDING_CACHE_FOLDER:
            cache_path = Path(settings.EMBEDDING_CACHE_FOLDER).resolve()
            hf_home = cache_path.parent if cache_path.name == "hub" else cache_path
            os.environ.setdefault("HF_HOME", str(hf_home))
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "Install requirements-embeddings.txt to use sentence_transformers"
            ) from exc
        if self.device.startswith("cuda"):
            import torch

            if not torch.cuda.is_available():
                raise RuntimeError(
                    "Embedding device is CUDA but PyTorch cannot access an NVIDIA GPU"
                )
        try:
            self._model = SentenceTransformer(
                self.configured_model_name,
                device=self.device,
                cache_folder=settings.EMBEDDING_CACHE_FOLDER,
                local_files_only=settings.EMBEDDING_LOCAL_FILES_ONLY,
            )
        except (MemoryError, OSError, RuntimeError) as exc:
            if not settings.EMBEDDING_ALLOW_DETERMINISTIC_FALLBACK or not is_embedding_model_unavailable_error(exc):
                raise
            logger.warning(
                "Embedding model %s could not be loaded because system memory is exhausted; "
                "falling back to deterministic %s-dimensional vectors for this process: %s",
                self.configured_model_name,
                self.dimension,
                exc,
            )
            self.backend = "deterministic"
            self._model = None
            return None
        dimension_getter = getattr(self._model, "get_embedding_dimension", None)
        model_dimension = (
            dimension_getter()
            if dimension_getter is not None
            else self._model.get_sentence_embedding_dimension()
        )
        if model_dimension != self.dimension:
            raise RuntimeError(
                f"Embedding model returned {model_dimension} dimensions; "
                f"database expects {self.dimension}"
            )
        return self._model

    def count_tokens(self, text: str) -> int:
        return self.count_tokens_batch([text])[0]

    def count_tokens_batch(self, texts: list[str]) -> list[int]:
        if not texts:
            return []
        ai_client = get_ai_service_client()
        if ai_client.enabled:
            counts: list[int] = []
            for start in range(0, len(texts), self.batch_size):
                batch = texts[start:start + self.batch_size]
                result = ai_client.count_tokens(batch)
                self._remote_max_input_tokens = int(result["max_input_tokens"])
                batch_counts = [int(count) for count in result["token_counts"]]
                if len(batch_counts) != len(batch):
                    raise RuntimeError("AI service token count does not match input count")
                counts.extend(batch_counts)
            return counts
        tokenizer = self._load_tokenizer()
        if tokenizer is None:
            return [len(_TOKEN_PATTERN.findall(text)) for text in texts]
        encoded = tokenizer(
            texts,
            add_special_tokens=True,
            truncation=False,
        )
        return [len(input_ids) for input_ids in encoded["input_ids"]]

    def _deterministic_embedding(self, text: str) -> list[float]:
        text_bytes = text.encode("utf-8")
        vector: list[float] = []
        for index in range(self.dimension):
            digest = hashlib.sha256(text_bytes + index.to_bytes(4, "big")).digest()
            value = (int.from_bytes(digest[:4], "big") / (2**32 - 1)) * 2.0 - 1.0
            vector.append(value)
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector

    def _embed_gemini(
        self, texts: list[str], *, input_type: str = "document"
    ) -> list[list[float]]:
        api_key = settings.GOOGLE_AI_API_KEY
        if not api_key:
            raise RuntimeError("GOOGLE_AI_API_KEY is required for Gemini embeddings")
        model = f"models/{self.configured_model_name.removeprefix('models/')}"
        task_type = (
            "RETRIEVAL_QUERY" if input_type == "query" else "RETRIEVAL_DOCUMENT"
        )
        vectors: list[list[float]] = []
        import httpx

        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            response = httpx.post(
                f"https://generativelanguage.googleapis.com/v1beta/{model}:batchEmbedContents",
                headers={
                    "x-goog-api-key": api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "requests": [
                        {
                            "model": model,
                            "content": {"parts": [{"text": text}]},
                            "taskType": task_type,
                            "outputDimensionality": self.dimension,
                        }
                        for text in batch
                    ]
                },
                timeout=60,
            )
            response.raise_for_status()
            embeddings = response.json().get("embeddings", [])
            if len(embeddings) != len(batch):
                raise RuntimeError("Gemini embedding result count mismatch")
            for row in embeddings:
                raw_vec = row.get("values", [])
                if len(raw_vec) != self.dimension:
                    raise RuntimeError(
                        f"Gemini returned {len(raw_vec)} dimensions; expected {self.dimension}"
                    )
                norm = math.sqrt(sum(v * v for v in raw_vec))
                vectors.append([v / norm for v in raw_vec] if norm else raw_vec)
        return vectors

    def _embed_once(
        self,
        texts: list[str],
        *,
        input_type: str = "document",
        completed_before: int = 0,
        total_count: int | None = None,
        progress_callback: Callable[[dict[str, int]], None] | None = None,
        progress_stream_id: str | None = None,
    ) -> list[list[float]]:
        ai_client = get_ai_service_client()
        if ai_client.enabled:
            vectors: list[list[float]] = []
            for start in range(0, len(texts), self.batch_size):
                batch = texts[start:start + self.batch_size]
                progress_options = (
                    {"progress_stream_id": progress_stream_id}
                    if progress_stream_id
                    else {}
                )
                result = ai_client.embed(
                    batch,
                    input_type=input_type,
                    completed_before=completed_before + start,
                    total_count=total_count or completed_before + len(texts),
                    **progress_options,
                )
                if int(result["dimension"]) != self.dimension:
                    raise RuntimeError("AI service embedding dimension mismatch")
                batch_vectors = list(result["vectors"])
                if len(batch_vectors) != len(batch):
                    raise RuntimeError("AI service embedding result count does not match input count")
                vectors.extend(batch_vectors)
                if progress_callback is not None:
                    progress_callback({
                        "batch_count": int(result.get("batch_count", len(batch_vectors))),
                        "embedded_count": int(
                            result.get("embedded_count", completed_before + len(vectors))
                        ),
                        "total_count": int(
                            result.get("total_count", total_count or completed_before + len(texts))
                        ),
                        "remaining_count": int(
                            result.get(
                                "remaining_count",
                                max((total_count or completed_before + len(texts)) - completed_before - len(vectors), 0),
                            )
                        ),
                    })
            return vectors
        if self.backend == "gemini":
            vectors = self._embed_gemini(texts, input_type=input_type)
        else:
            model = self._load_model()
            if model is None:
                vectors = [self._deterministic_embedding(text) for text in texts]
            else:
                encoded = model.encode(
                    texts,
                    batch_size=self.batch_size,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                    convert_to_numpy=True,
                )
                vectors = encoded.tolist()
        if progress_callback is not None:
            resolved_total = total_count or completed_before + len(vectors)
            embedded_count = min(completed_before + len(vectors), resolved_total)
            progress_callback({
                "batch_count": len(vectors),
                "embedded_count": embedded_count,
                "total_count": resolved_total,
                "remaining_count": max(resolved_total - embedded_count, 0),
            })
        return vectors

    def embed_texts(
        self,
        texts: list[str],
        *,
        input_type: str = "document",
        completed_before: int = 0,
        total_count: int | None = None,
        progress_callback: Callable[[dict[str, int]], None] | None = None,
        progress_stream_id: str | None = None,
    ) -> list[list[float]]:
        if not texts:
            return []
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                vectors = self._embed_once(
                    texts,
                    input_type=input_type,
                    completed_before=completed_before,
                    total_count=total_count,
                    progress_callback=progress_callback,
                    progress_stream_id=progress_stream_id,
                )
                if len(vectors) != len(texts):
                    raise RuntimeError("Embedding result count does not match input count")
                if any(len(vector) != self.dimension for vector in vectors):
                    raise RuntimeError("Embedding vector dimension mismatch")
                return vectors
            except RuntimeError as exc:
                last_error = exc
                if attempt + 1 < self.max_retries:
                    time.sleep(2**attempt)
        raise RuntimeError(
            f"Embedding failed after {self.max_retries} attempts: {last_error}"
        ) from last_error

    def embed_query(self, question: str) -> list[float]:
        ai_client = get_ai_service_client()
        if ai_client.enabled:
            result = ai_client.embed([question], input_type="query")
            if int(result["dimension"]) != self.dimension:
                raise RuntimeError("AI service embedding dimension mismatch")
            return list(result["vectors"][0])
        if self.backend == "gemini":
            return self._embed_gemini([question], input_type="query")[0]
        query_text = (
            "Truy xuất tài liệu nội bộ phù hợp để trả lời câu hỏi:\n"
            f"{question.strip()}"
        )
        return self.embed_texts([query_text], input_type="query")[0]


@lru_cache(maxsize=1)
def get_embedding_service() -> EmbeddingService:
    return EmbeddingService()
