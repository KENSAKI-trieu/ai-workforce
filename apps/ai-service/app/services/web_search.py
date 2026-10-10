"""Web search for agents: Gemini answering with Google Search grounding.

Only Gemini can search here, whatever model the agent itself runs on. What comes back is
the model's summary and the pages Google grounded it in, each with the sentences of the
summary that page supports. A summary the model wrote without searching is dropped: it
would come from the model's memory while looking like a web result.

Google hands the pages back as redirect links on vertexaisearch.cloud.google.com; they
are resolved to the real address so a reader sees where a claim came from.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urlparse

import httpx
from google import genai
from google.genai import types

from app.core.config import settings

logger = logging.getLogger(__name__)

_REDIRECT_HOST = "vertexaisearch.cloud.google.com"
_SNIPPET_CHARS = 700
_MARKDOWN_RE = re.compile(r"\*\*|__|^\s*[*\-•]\s+", re.MULTILINE)

SYSTEM_INSTRUCTION = (
    "Bạn là trợ lý nghiên cứu. Luôn dùng Google Search để tìm thông tin mới nhất trước khi "
    "trả lời. Chỉ nêu điều có trong kết quả tìm kiếm, kèm thời điểm khi nguồn có ghi; không "
    "suy đoán, không bổ sung từ hiểu biết riêng. Trả lời bằng tiếng Việt, ngắn gọn, dạng gạch "
    "đầu dòng, vào thẳng nội dung: không câu dẫn nhập, không kể mình sẽ tìm gì."
)


class WebSearchUnavailable(RuntimeError):
    """No key, every key refused, or the vendor failed."""


def _resolve(client: httpx.Client, url: str) -> str:
    if urlparse(url).hostname != _REDIRECT_HOST:
        return url
    try:
        response = client.head(url)
        location = response.headers.get("location")
        if response.is_redirect and location:
            return location
    except httpx.HTTPError:
        logger.info("Could not resolve a grounding redirect", exc_info=True)
    return url


def _plain(text: str) -> str:
    """A snippet as plain text: the summary's bold marks and bullets do not belong in it."""
    return " ".join(_MARKDOWN_RE.sub("", text).split())


def _site(url: str) -> str:
    host = urlparse(url).hostname or ""
    return host[4:] if host.startswith("www.") else host


def _generate(query: str) -> Any:
    keys = settings.google_api_keys
    if not keys:
        raise WebSearchUnavailable("GOOGLE_AI_API_KEY is not configured")
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        tools=[types.Tool(google_search=types.GoogleSearch())],
        http_options=types.HttpOptions(timeout=int(settings.WEB_SEARCH_TIMEOUT_SECONDS * 1000)),
    )
    failure: Exception | None = None
    # A key out of quota is common on the free tier; the next one may still answer.
    for key in keys:
        # Held for the whole call: an unreferenced Client closes its connection pool when
        # it is collected, which can happen before the request is sent.
        client = genai.Client(api_key=key)
        try:
            return client.models.generate_content(
                model=settings.WEB_SEARCH_MODEL, contents=query, config=config
            )
        except Exception as exc:  # noqa: BLE001 - every vendor error means "try the next key"
            failure = exc
            logger.warning("Web search failed on one key: %s", type(exc).__name__)
        finally:
            client.close()
    raise WebSearchUnavailable("Every Google AI key refused the web search") from failure


def _usage(response: Any) -> dict[str, int]:
    metadata = getattr(response, "usage_metadata", None)
    if metadata is None:
        return {"prompt_tokens": 0, "completion_tokens": 0}
    # Gemini bills the search tool's own prompt as input and its thinking as output.
    return {
        "prompt_tokens": int(metadata.prompt_token_count or 0) + int(metadata.tool_use_prompt_token_count or 0),
        "completion_tokens": int(metadata.candidates_token_count or 0) + int(metadata.thoughts_token_count or 0),
        "cached_prompt_tokens": int(metadata.cached_content_token_count or 0),
    }


def search_web(query: str, *, max_results: int = 6) -> dict[str, Any]:
    """{summary, queries, results: [{title, url, site, snippet}], provider, model, usage}."""
    response = _generate(query)
    candidate = (response.candidates or [None])[0]
    metadata = getattr(candidate, "grounding_metadata", None)
    chunks = list(getattr(metadata, "grounding_chunks", None) or [])
    supports = list(getattr(metadata, "grounding_supports", None) or [])

    snippets: dict[int, list[str]] = {}
    for support in supports:
        text = (getattr(support.segment, "text", None) or "").strip()
        for index in support.grounding_chunk_indices or []:
            bucket = snippets.setdefault(index, [])
            if text and text not in bucket:
                bucket.append(text)

    # Pages that back a sentence of the summary first, then the rest Google returned.
    candidates = [
        (index, chunk.web)
        for index, chunk in enumerate(chunks)
        if getattr(chunk, "web", None) is not None and chunk.web.uri
    ]
    candidates.sort(key=lambda item: item[0] not in snippets)
    candidates = candidates[: max(1, max_results) * 2]

    with httpx.Client(timeout=5.0, follow_redirects=False) as client, ThreadPoolExecutor(max_workers=8) as pool:
        urls = list(pool.map(lambda item: _resolve(client, item[1].uri), candidates))

    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for (index, web), url in zip(candidates, urls):
        if url in seen:
            continue
        seen.add(url)
        # Cleaned one sentence at a time: a bullet only marks the start of a segment.
        snippet = " ".join(_plain(text) for text in snippets.get(index, []))
        results.append({
            "title": (web.title or _site(url)).strip(),
            "url": url,
            "site": _site(url),
            "snippet": snippet[:_SNIPPET_CHARS],
        })
        if len(results) >= max_results:
            break

    grounded = bool(results)
    return {
        "summary": (response.text or "").strip() if grounded else "",
        "queries": list(getattr(metadata, "web_search_queries", None) or []),
        "results": results,
        "grounded": grounded,
        "provider": "gemini",
        "model": settings.WEB_SEARCH_MODEL,
        "usage": _usage(response),
    }
