"""The model steps of a campaign: outline, three posts, fact-check, refine.

Ported from market-agent's LangGraph nodes, minus the graph: the two stops for the author
are the campaign row's stages (see campaigns.py), so each request runs the steps between
two stops and returns. Every call goes through the AI service on the Marketing agent's
model and is metered; personal data in what the user wrote reaches the provider as
stand-ins ([SĐT_1], [NGƯỜI_1]) and is put back in what comes out.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from app.agents.llm_json import is_echo_provider, report_usage
from app.core.config import settings
from app.domains.legal.contract_privacy import Pseudonymizer
from app.domains.marketing.guardrails import find_cliches, redact_secrets
from app.domains.marketing.prompts import external_context, load_prompt

logger = logging.getLogger(__name__)

PLATFORMS = ("facebook", "instagram", "threads")
PLATFORM_LABELS = {"facebook": "Facebook", "instagram": "Instagram", "threads": "Threads"}
# Rewrites after a failed fact-check before the author sees what is left.
MAX_REFINES = 2

PLATFORM_RULES = {
    "facebook": "Kênh: Facebook. 80–180 từ, giọng gần gũi, đoạn ngắn, có thể dùng 1–3 emoji; "
    "kết bằng call-to-action rõ ràng.",
    "instagram": "Kênh: Instagram. Caption 60–150 từ, giọng trẻ trung, giàu hình ảnh. Dòng đầu (dưới 125 ký tự) "
    "là hook vì bị cắt sau \"... xem thêm\"; đoạn ngắn, 2–4 emoji; kết bằng call-to-action (vd. link ở bio, "
    "lưu bài, nhắn tin); 5–10 hashtag ở cuối, trộn hashtag tiếng Việt và ngách. Mở đầu bằng một dòng "
    "[Gợi ý hình ảnh: ...] mô tả ảnh/carousel đi kèm.",
    "threads": "Kênh: Threads. Một chuỗi 3–5 bài, mỗi bài tối đa 500 ký tự, đánh số 1/, 2/, ..., các bài cách nhau "
    "một dòng trống; giọng trò chuyện, thẳng thắn như đang tán gẫu; bài đầu là hook gây tò mò hoặc nêu "
    "quan điểm; bài cuối mời bình luận hoặc call-to-action; tối đa 1 hashtag (topic tag) cho cả chuỗi.",
}

UNREADABLE_CHECK = "Không đọc được kết quả fact-check, cần kiểm tra thủ công."

# stage, status ("running" | "done"), detail
Progress = Callable[[str, str, str | None], None]


def no_progress(_stage: str, _status: str, _detail: str | None = None) -> None:
    return None


class MarketingModelUnavailable(RuntimeError):
    """The AI service gave no usable answer, so the step produced nothing."""


@dataclass
class Writer:
    """Model access for the steps of one request.

    One Pseudonymizer per request: the brief is always hidden first, so the same person or
    phone number gets the same stand-in in every step, and in a later request too.
    """

    client: Any
    on_usage: Callable[[dict[str, Any]], None] | None = None
    privacy: Pseudonymizer = field(default_factory=Pseudonymizer)
    timeout: float | None = None

    def complete(self, system: str, user: str) -> tuple[str, dict[str, Any]]:
        """Thread-safe: it touches no database session. Metering is `record`'s job."""
        if not getattr(self.client, "enabled", True):
            raise MarketingModelUnavailable("AI service is not configured")
        result = self.client.generate_text(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            timeout=self.timeout or settings.AI_SERVICE_TIMEOUT_SECONDS,
        )
        if not isinstance(result, dict) or is_echo_provider(result):
            raise MarketingModelUnavailable("No model answered")
        text = str(result.get("content") or "").strip()
        if not text:
            raise MarketingModelUnavailable("The model returned nothing")
        return text, result

    def record(self, result: dict[str, Any]) -> None:
        report_usage(self.on_usage, result)

    def hide(self, text: str | None) -> str:
        return self.privacy.hide(text or "")

    def reveal(self, value: Any) -> Any:
        return self.privacy.reveal(value)


def _brief_block(writer: Writer, brief: str) -> str:
    return f"Brief chiến dịch:\n{writer.hide(brief)}"


def write_outline(
    writer: Writer,
    *,
    brief: str,
    context: list[str],
    previous_outline: str | None = None,
    feedback: str | None = None,
) -> str:
    parts = [external_context(context), _brief_block(writer, brief)]
    if feedback:
        parts.append(f"Dàn ý trước đã bị từ chối:\n{writer.hide(previous_outline)}")
        parts.append(f"Phản hồi của người dùng (ưu tiên làm theo):\n{writer.hide(feedback)}")
    text, result = writer.complete(load_prompt("outline"), "\n\n".join(parts))
    writer.record(result)
    return writer.reveal(text)


def _source_block(writer: Writer, brief: str, outline: str, context: list[str]) -> str:
    """What every post is written and checked against: documents, brief, approved outline."""
    return "\n\n".join([
        external_context(context),
        _brief_block(writer, brief),
        f"Dàn ý đã duyệt:\n{writer.hide(outline)}",
    ])


def _write_in_parallel(writer: Writer, jobs: dict[str, tuple[str, str]]) -> dict[str, str]:
    """One post per platform, at once. Usage is metered here, on the caller's thread."""
    if not jobs:
        return {}
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        # A fresh copy of the caller's context per call: the agent's model travels in it.
        futures = {
            platform: pool.submit(contextvars.copy_context().run, writer.complete, system, user)
            for platform, (system, user) in jobs.items()
        }
    posts: dict[str, str] = {}
    failure: BaseException | None = None
    for platform, future in futures.items():
        try:
            text, result = future.result()
        except BaseException as exc:  # noqa: BLE001 - re-raised below, after metering the rest
            failure = failure or exc
            continue
        writer.record(result)
        cleaned, secrets = redact_secrets(writer.reveal(text))
        if secrets:
            logger.warning("Redacted %s from the %s post", secrets, platform)
        posts[platform] = cleaned
    if failure is not None:
        raise failure
    return posts


def write_posts(writer: Writer, *, brief: str, outline: str, context: list[str]) -> dict[str, str]:
    system, sources = load_prompt("generator"), _source_block(writer, brief, outline, context)
    return _write_in_parallel(
        writer, {platform: (system, f"{sources}\n\n{PLATFORM_RULES[platform]}") for platform in PLATFORMS}
    )


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$")


def parse_checker_output(text: str) -> tuple[bool, str, list[dict[str, str]]] | None:
    """The fact-checker's JSON, read through code fences and chatter; None if unreadable."""
    text = _FENCE_RE.sub("", (text or "").strip())
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("issues", []), list):
        return None
    issues = [
        {
            "platform": str(item.get("platform", "")).lower(),
            "kind": "fact",
            "claim": str(item.get("claim", "")),
            "problem": str(item.get("problem", "")),
            "suggestion": str(item.get("suggestion", "")),
        }
        for item in data.get("issues", [])
        if isinstance(item, dict)
    ]
    passed = bool(data.get("passed", not issues)) and not issues
    return passed, str(data.get("summary", "")), issues


def cliche_issues(posts: dict[str, str]) -> list[dict[str, str]]:
    return [
        {
            "platform": platform,
            "kind": "cliche",
            "claim": phrase,
            "problem": "Cụm từ sáo rỗng kiểu AI",
            "suggestion": "Thay bằng chi tiết cụ thể về sản phẩm hoặc khách hàng",
        }
        for platform in PLATFORMS
        for phrase in find_cliches(posts.get(platform, ""))
    ]


def fact_check(
    writer: Writer,
    *,
    brief: str,
    outline: str,
    context: list[str],
    posts: dict[str, str],
    round_: int,
) -> dict[str, Any]:
    """Posts against the sources (model) and against stock phrases (regex).

    `passed` only when the checker was read and found nothing. An unreadable checker is
    not a pass: the report says so, and with nothing specific to fix no rewrite follows.
    """
    drafts_xml = "\n\n".join(
        f'<draft platform="{platform}">\n{writer.hide(posts.get(platform, ""))}\n</draft>'
        for platform in PLATFORMS
    )
    text, result = writer.complete(
        load_prompt("fact_checker"),
        f"NGUỒN:\n{_source_block(writer, brief, outline, context)}\n\nBẢN THẢO CẦN KIỂM TRA:\n{drafts_xml}",
    )
    writer.record(result)
    parsed = parse_checker_output(text)
    if parsed is None:
        logger.warning("Marketing fact-check output was unreadable")
        summary, issues, readable = UNREADABLE_CHECK, [], False
    else:
        _passed, summary, issues = parsed
        summary, issues, readable = writer.reveal(summary), writer.reveal(issues), True
    issues = issues + cliche_issues(posts)
    return {
        "passed": readable and not issues,
        "checked": readable,
        "summary": summary,
        "issues": issues,
        "round": round_,
    }


def _issue_lines(issues: list[dict[str, str]]) -> str:
    return "\n".join(f"- \"{item['claim']}\": {item['problem']}. Gợi ý: {item['suggestion']}" for item in issues)


def refine_posts(
    writer: Writer,
    *,
    brief: str,
    outline: str,
    context: list[str],
    posts: dict[str, str],
    issues: list[dict[str, str]],
) -> tuple[dict[str, str], list[str]]:
    """Rewrite only the posts with issues; an issue naming no known platform touches all."""
    by_platform = {
        platform: [item for item in issues if item["platform"] == platform or item["platform"] not in PLATFORMS]
        for platform in PLATFORMS
    }
    targets = [platform for platform in PLATFORMS if by_platform[platform]]
    system, sources = load_prompt("refine"), _source_block(writer, brief, outline, context)
    rewritten = _write_in_parallel(writer, {
        platform: (
            system,
            f"{sources}\n\n{PLATFORM_RULES[platform]}\n\nBài hiện tại:\n{writer.hide(posts.get(platform, ''))}"
            f"\n\nVấn đề cần sửa:\n{writer.hide(_issue_lines(by_platform[platform]))}",
        )
        for platform in targets
    })
    return {**posts, **rewritten}, targets


def draft_and_check(
    writer: Writer,
    *,
    brief: str,
    outline: str,
    context: list[str],
    progress: Progress = no_progress,
) -> tuple[dict[str, str], dict[str, Any], int]:
    """Three posts, checked and rewritten until clean or MAX_REFINES rewrites are spent."""
    progress("DRAFTS", "running", "Facebook · Instagram · Threads")
    posts = write_posts(writer, brief=brief, outline=outline, context=context)
    progress("DRAFTS", "done", None)
    rounds = 0
    while True:
        progress("FACT_CHECK", "running", f"Vòng {rounds + 1}")
        report = fact_check(writer, brief=brief, outline=outline, context=context, posts=posts, round_=rounds)
        progress("FACT_CHECK", "done", "Đạt" if report["passed"] else f"{len(report['issues'])} vấn đề")
        if report["passed"] or not report["issues"] or rounds >= MAX_REFINES:
            return posts, report, rounds
        progress("REFINE", "running", f"Lần sửa {rounds + 1}/{MAX_REFINES}")
        posts, targets = refine_posts(
            writer, brief=brief, outline=outline, context=context, posts=posts, issues=report["issues"]
        )
        rounds += 1
        progress("REFINE", "done", ", ".join(PLATFORM_LABELS[platform] for platform in targets))
