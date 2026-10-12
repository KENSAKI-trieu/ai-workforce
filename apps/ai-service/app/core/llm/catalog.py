"""The chat models an administrator can pick for an agent, read from the vendors' own APIs.

Each vendor lists far more than chat models -- speech, images, embeddings, realtime audio.
Only models that answer a plain chat completion through LangChain are offered, because
that is the only way the graph and /v1/llm/generate call them. A vendor whose listing
fails still offers its configured default, so a transient outage does not empty the
picker; the failure is reported alongside instead.

A model name also has to say which vendor serves it: the backend sends a name only, and
handing a Gemini name to OpenAI fails the call and drops to a fallback with its default.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from app.core.config import settings
from app.core.llm.credentials import fingerprint as credentials_fingerprint
from app.core.llm.factory import PROVIDER_ORDER, default_model_for, provider_keys

logger = logging.getLogger(__name__)

CATALOG_TTL_SECONDS = 600.0
LISTING_TIMEOUT_SECONDS = 15.0

# Name fragments of models that are not plain chat models, or that LangChain's chat
# completion call cannot drive (OpenAI's `-pro` models answer the Responses API only).
_NON_CHAT_FRAGMENTS: dict[str, tuple[str, ...]] = {
    "gemini": (
        "tts", "image", "embedding", "transcribe", "live", "native-audio", "robotics",
        "computer-use", "omni", "customtools",
    ),
    "openai": (
        "audio", "realtime", "transcribe", "tts", "image", "search", "instruct", "codex",
        "live", "embedding", "moderation", "-pro", "-16k",
    ),
}
# Dated snapshots (`gpt-4o-2024-08-06`, `gpt-3.5-turbo-0125`) duplicate their alias.
_DATED_SNAPSHOT = re.compile(r"-(\d{4}-\d{2}-\d{2}|\d{4})$")
_OPENAI_CHAT = re.compile(r"^(gpt-\d|o\d)")


@dataclass(frozen=True)
class CatalogModel:
    id: str
    provider: str
    label: str

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "provider": self.provider, "label": self.label}


def provider_for_model(model: str | None) -> str | None:
    """The vendor that serves this model name, or None when no vendor claims it."""
    name = (model or "").strip().lower()
    if not name:
        return None
    if name.startswith("gemini"):
        return "gemini"
    if _OPENAI_CHAT.match(name) or name.startswith("chatgpt"):
        return "openai"
    if "anthropic." in name or name == settings.BEDROCK_CHAT_MODEL.lower():
        return "bedrock"
    return None


def _is_chat_model(provider: str, name: str) -> bool:
    lowered = name.lower()
    if any(fragment in lowered for fragment in _NON_CHAT_FRAGMENTS.get(provider, ())):
        return False
    if _DATED_SNAPSHOT.search(lowered):
        return False
    if provider == "gemini":
        return lowered.startswith("gemini-")
    if provider == "openai":
        return bool(_OPENAI_CHAT.match(lowered))
    return False


def _list_gemini(api_key: str) -> list[CatalogModel]:
    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=int(LISTING_TIMEOUT_SECONDS * 1000)),
    )
    models: list[CatalogModel] = []
    for item in client.models.list():
        name = str(item.name or "").removeprefix("models/")
        actions = item.supported_actions or []
        if "generateContent" in actions and _is_chat_model("gemini", name):
            models.append(CatalogModel(name, "gemini", str(item.display_name or name)))
    return models


def _list_openai(api_key: str) -> list[CatalogModel]:
    from openai import OpenAI

    client = OpenAI(api_key=api_key, timeout=LISTING_TIMEOUT_SECONDS)
    return [
        CatalogModel(item.id, "openai", item.id)
        for item in client.models.list()
        if _is_chat_model("openai", item.id)
    ]


Lister = Callable[[str], list[CatalogModel]]
LISTERS: dict[str, Lister] = {"gemini": _list_gemini, "openai": _list_openai}


def default_model() -> dict[str, str] | None:
    """The model a turn uses when the agent names none: the leading configured vendor's."""
    configured = settings.LLM_DEFAULT_PROVIDER
    if configured == "local":
        return None
    order = [configured] if configured != "auto" else []
    order.extend(item for item in PROVIDER_ORDER if item not in order)
    for provider in order:
        if provider_keys(provider):
            return {"provider": provider, "id": default_model_for(provider)}  # type: ignore[arg-type]
    return None


def verify_key(provider: str, api_key: str, listers: dict[str, Lister] | None = None) -> dict:
    """Whether a key a company is about to save answers its vendor's model listing."""
    lister = (listers if listers is not None else LISTERS).get(provider)
    if lister is None:
        return {"ok": False, "error": "UnsupportedProvider", "models": 0}
    try:
        listed = lister(api_key)
    except Exception as exc:  # any vendor failure means the key cannot be used as it is
        logger.warning("Verifying a %s key failed with %s", provider, exc.__class__.__name__)
        return {"ok": False, "error": exc.__class__.__name__, "models": 0}
    return {"ok": True, "error": None, "models": len(listed)}


class ModelCatalog:
    """Vendor listings, cached for a while: the picker is opened far more than it changes.

    Cached per set of keys: a company that entered its own key sees what that key serves,
    and its listing never answers for another company.
    """

    MAX_CACHED = 64

    def __init__(self, listers: dict[str, Lister] | None = None, ttl: float = CATALOG_TTL_SECONDS) -> None:
        self._listers = listers if listers is not None else LISTERS
        self._ttl = ttl
        self._lock = threading.Lock()
        self._cached: dict[str, tuple[float, dict]] = {}

    def snapshot(self, *, refresh: bool = False) -> dict:
        cache_key = credentials_fingerprint()
        with self._lock:
            cached = self._cached.get(cache_key)
            if not refresh and cached and time.monotonic() - cached[0] < self._ttl:
                return cached[1]
        result = self._build()
        with self._lock:
            if len(self._cached) >= self.MAX_CACHED and cache_key not in self._cached:
                oldest = min(self._cached, key=lambda key: self._cached[key][0])
                self._cached.pop(oldest, None)
            self._cached[cache_key] = (time.monotonic(), result)
        return result

    def _build(self) -> dict:
        models: dict[str, CatalogModel] = {}
        errors: dict[str, str] = {}
        for provider in PROVIDER_ORDER:
            keys = provider_keys(provider)
            if not keys:
                continue
            fallback = default_model_for(provider)  # type: ignore[arg-type]
            lister = self._listers.get(provider)
            listed: list[CatalogModel] = []
            if lister is not None:
                for api_key in keys:
                    try:
                        listed = lister(api_key)
                        break
                    except Exception as exc:  # any vendor failure: try the next key
                        errors[provider] = exc.__class__.__name__
                        logger.warning("Listing %s models failed with %s", provider, exc.__class__.__name__)
                else:
                    listed = []
                if listed:
                    errors.pop(provider, None)
            for item in listed:
                models.setdefault(item.id, item)
            # Always offered: it is what this vendor runs today without any choice made.
            models.setdefault(fallback, CatalogModel(fallback, provider, fallback))
        ordered = sorted(
            models.values(),
            key=lambda item: (PROVIDER_ORDER.index(item.provider), item.id),  # type: ignore[arg-type]
        )
        return {
            "default": default_model(),
            "models": [item.as_dict() for item in ordered],
            "errors": errors,
        }


model_catalog = ModelCatalog()
