"""Load Vietnam's accounting and invoicing law into a tenant's knowledge base for Finance.

Downloads each document from the Official Gazette (congbao.chinhphu.vn) -- the issue PDFs
carry a text layer, unlike the signed scans on vanban.chinhphu.vn -- joins its parts into
one PDF, uploads it through the documents API into one collection, and ticks that
collection for the Finance agent (and the CEO assistant, which answers the same questions).

The list is what is in force on 2026-10-12. Thông tư 200/2014 and Thông tư 78/2021 are left
out on purpose: Thông tư 99/2025 replaced the first from 01/01/2026, and Thông tư 32/2025
the second from 01/06/2025. Review the list when the law changes.

Usage (from backend/):
  .venv/Scripts/python.exe scripts/load_accounting_law.py --api http://127.0.0.1:8000 \\
      --email admin@company.com --password '...'
"""

from __future__ import annotations

import argparse
import html
import io
import re
import sys
import time
import urllib.request
from dataclasses import dataclass

import httpx
from pypdf import PdfReader, PdfWriter

COLLECTION = "Pháp luật Kế toán - Hoá đơn"
AGENTS = ("FINANCE", "CEO")
USER_AGENT = "Mozilla/5.0 (AI Workforce knowledge loader)"
MAX_BYTES = 9_500_000  # the upload API takes 10 MB per file


@dataclass(frozen=True)
class Document:
    document_id: str
    title: str
    page: str  # the Official Gazette page that lists the issue PDFs
    effective_date: str
    version: str


DOCUMENTS = (
    Document(
        "luat-ke-toan-vbhn-25-2025",
        "Luật Kế toán (Văn bản hợp nhất 25/VBHN-VPQH năm 2025)",
        "https://congbao.chinhphu.vn/tai-ve-van-ban-so-25-vbhn-vpqh-44457-55466?format=pdf",
        "2017-01-01", "2025",
    ),
    Document(
        "thong-tu-99-2025-tt-btc",
        "Thông tư 99/2025/TT-BTC hướng dẫn Chế độ kế toán doanh nghiệp",
        "https://congbao.chinhphu.vn/van-ban/thong-tu-so-99-2025-tt-btc-46529.htm",
        "2026-01-01", "2025",
    ),
    Document(
        "thong-tu-133-2016-tt-btc",
        "Thông tư 133/2016/TT-BTC hướng dẫn Chế độ kế toán doanh nghiệp nhỏ và vừa",
        "https://congbao.chinhphu.vn/van-ban/thong-tu-so-133-2016-tt-btc-21048.htm",
        "2017-01-01", "2016",
    ),
    Document(
        "nghi-dinh-123-2020-nd-cp",
        "Nghị định 123/2020/NĐ-CP quy định về hóa đơn, chứng từ",
        "https://congbao.chinhphu.vn/van-ban/nghi-dinh-so-123-2020-nd-cp-32290.htm",
        "2022-07-01", "2020",
    ),
    Document(
        "nghi-dinh-70-2025-nd-cp",
        "Nghị định 70/2025/NĐ-CP sửa đổi, bổ sung Nghị định 123/2020/NĐ-CP về hóa đơn, chứng từ",
        "https://congbao.chinhphu.vn/van-ban/nghi-dinh-so-70-2025-nd-cp-44623.htm",
        "2025-06-01", "2025",
    ),
    Document(
        "thong-tu-32-2025-tt-btc",
        "Thông tư 32/2025/TT-BTC hướng dẫn về hóa đơn, chứng từ",
        "https://congbao.chinhphu.vn/van-ban/thong-tu-so-32-2025-tt-btc-45039.htm",
        "2025-06-01", "2025",
    ),
)

_CDN = re.compile(r'"(https://congbaocdn\.chinhphu\.vn/[^"]+?\.pdf)"')
_STREAM = re.compile(r'"(https://g7\.cdnchinhphu\.vn/api/download/stream\?Url=[^"]+?\.pdf)"')


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=180) as response:  # noqa: S310 - fixed government hosts
        return response.read()


def issue_pdfs(page_url: str) -> list[str]:
    """The issue PDFs a Gazette page lists, in issue order; the direct CDN copy when there is one."""
    page = _get(page_url).decode("utf-8", errors="replace")
    links = list(dict.fromkeys(_CDN.findall(page)))
    if not links:
        links = list(dict.fromkeys(html.unescape(link) for link in _STREAM.findall(page)))
    numbers = lambda url: [int(n) for n in re.findall(r"(\d{4,})", url.rsplit("/", 1)[-1])] or [0]  # noqa: E731
    return sorted(links, key=numbers)


def joined_pdf(urls: list[str]) -> bytes:
    writer = PdfWriter()
    for url in urls:
        data = _get(url)
        if not data.startswith(b"%PDF"):
            raise RuntimeError(f"Not a PDF: {url}")
        writer.append(PdfReader(io.BytesIO(data)))
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def text_share(data: bytes) -> float:
    pages = PdfReader(io.BytesIO(data)).pages
    return sum(1 for page in pages if len((page.extract_text() or "").strip()) > 80) / max(len(pages), 1)


def bundles(data: bytes) -> list[bytes]:
    """The PDF split into parts under the upload limit, by whole pages."""
    if len(data) <= MAX_BYTES:
        return [data]
    reader = PdfReader(io.BytesIO(data))
    parts, writer = [], PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
        buffer = io.BytesIO()
        writer.write(buffer)
        if buffer.tell() > MAX_BYTES and len(writer.pages) > 1:
            last = PdfWriter()
            for kept in writer.pages[:-1]:
                last.add_page(kept)
            out = io.BytesIO()
            last.write(out)
            parts.append(out.getvalue())
            writer = PdfWriter()
            writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    parts.append(out.getvalue())
    return parts


def states(client: httpx.Client) -> dict[str, str]:
    listed = client.get("/api/v1/documents/")
    listed.raise_for_status()
    return {
        item["document_id"]: item.get("processing_status") or ""
        for item in listed.json()
        if item.get("collection_name") == COLLECTION
    }


def wait_for(client: httpx.Client, document_id: str, limit_seconds: int = 2 * 3600) -> str:
    """The document's final processing status, followed every 20 seconds."""
    started, last = time.monotonic(), ""
    while time.monotonic() - started < limit_seconds:
        status = states(client).get(document_id, "")
        if status != last:
            print(f"   [{int(time.monotonic() - started)}s] {document_id}: {status}", flush=True)
            last = status
        if status in {"ready", "failed"}:
            return status
        time.sleep(20)
    return last


def resume(client: httpx.Client, document_id: str, version: str, attempts: int = 6) -> str:
    """Resume a failed ingestion from its checkpoint, waiting out the embedding quota."""
    for attempt in range(1, attempts + 1):
        response = client.post(
            f"/api/v1/documents/{document_id}/retry", params={"version": version}, timeout=None,
        )
        if response.is_success:
            return "ready"
        print(f"   retry {attempt}/{attempts} for {document_id}: {response.status_code} {response.text[:160]}", flush=True)
        # The embedding API's per-minute quota: a pause lets the next try go further.
        time.sleep(90)
    return states(client).get(document_id, "failed")


def upload(client: httpx.Client, document: Document, name: str, part: bytes, document_id: str, title: str) -> bool:
    response = client.post(
        "/api/v1/documents/upload",
        files={"file": (name, part, "application/pdf")},
        data={
            "department_access": "ALL",
            "collection_name": COLLECTION,
            "document_id": document_id,
            "document_title": title,
            "document_type": "law",
            "version": document.version,
            "effective_date": document.effective_date,
            "confidentiality": "public",
            "duplicate_strategy": "replace",
            # Embedding a long circular takes many minutes: processed in the background
            # and followed, one document at a time so the embedding quota holds.
            "async_processing": "true",
        },
    )
    print(f"{'OK' if response.is_success else '!!'} {name}: {len(part) / 1e6:.1f} MB -> {response.status_code}", flush=True)
    return response.is_success


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", required=True, help="Backend base URL, e.g. http://127.0.0.1:8000")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--only", help="Comma-separated document ids to load")
    parser.add_argument("--reload", action="store_true", help="Upload again even what is already indexed")
    args = parser.parse_args()

    client = httpx.Client(base_url=args.api.rstrip("/"), timeout=900)
    login = client.post("/api/v1/auth/login", json={"email": args.email, "password": args.password})
    login.raise_for_status()
    client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"

    wanted = set(args.only.split(",")) if args.only else None
    failed: list[str] = []
    for document in DOCUMENTS:
        if wanted and document.document_id not in wanted:
            continue
        known = states(client)
        # Already there: done, or resumed from where it stopped rather than embedded again.
        present = sorted(key for key in known if key == document.document_id or key.startswith(f"{document.document_id}-phan-"))
        if present and not args.reload:
            for document_id in present:
                status = known[document_id]
                if status != "ready":
                    print(f"-> resuming {document_id} ({status})", flush=True)
                    status = resume(client, document_id, document.version)
                print(f"{'=' if status == 'ready' else '!'} {document_id}: {status}", flush=True)
                if status != "ready":
                    failed.append(document_id)
            continue
        urls = issue_pdfs(document.page)
        if not urls:
            print(f"! {document.document_id}: no PDF listed on {document.page}")
            failed.append(document.document_id)
            continue
        data = joined_pdf(urls)
        share = text_share(data)
        if share < 0.5:
            print(f"! {document.document_id}: only {share:.0%} of pages carry text; skipped")
            failed.append(document.document_id)
            continue
        parts = bundles(data)
        print(f"{document.document_id}: {len(urls)} issue PDF(s), text on {share:.0%} of pages", flush=True)
        for index, part in enumerate(parts, start=1):
            suffix = f"-phan-{index}" if len(parts) > 1 else ""
            title = f"{document.title} (phần {index}/{len(parts)})" if len(parts) > 1 else document.title
            document_id = f"{document.document_id}{suffix}"
            if not upload(client, document, f"{document_id}.pdf", part, document_id, title):
                failed.append(document_id)
                continue
            status = wait_for(client, document_id)
            if status == "failed":
                status = resume(client, document_id, document.version)
            if status != "ready":
                failed.append(document_id)

    if failed:
        print(f"! Not indexed yet: {', '.join(failed)}. Run again later to resume them; agents not ticked.")
        return 1

    selector = f"collection:{COLLECTION}"
    for role in AGENTS:
        agent = client.get(f"/api/v1/agents/{role}")
        if not agent.is_success:
            print(f"! agent {role}: {agent.status_code}")
            continue
        current = [item for item in agent.json().get("knowledge_access") or [] if item != "none"]
        if selector in current:
            print(f"= {role} already reads {COLLECTION}")
            continue
        updated = client.patch(f"/api/v1/agents/{role}", json={"knowledge_access": [*current, selector]})
        print(f"{'OK' if updated.is_success else '!!'} {role} now reads {COLLECTION}: {updated.status_code}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
