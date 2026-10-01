import { authorizedFetch } from "@/lib/authorizedFetch";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export type ReviewStageStatus = "pending" | "running" | "done" | "failed" | "skipped";

export interface ReviewProgressEvent {
  stage: string;
  status: Exclude<ReviewStageStatus, "pending">;
  detail?: string | null;
  elapsed_ms: number;
}

export type ReviewStreamEvent =
  | { event: "progress"; data: ReviewProgressEvent }
  | { event: "complete"; data: Record<string, unknown> }
  | { event: "error"; data: { message: string; status_code?: number } };

function parseBlock(block: string): ReviewStreamEvent | null {
  let event = "message";
  const lines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    if (line.startsWith("data:")) lines.push(line.slice(5).trimStart());
  }
  if (!lines.length) return null; // keep-alive comments carry no data
  return { event, data: JSON.parse(lines.join("\n")) } as ReviewStreamEvent;
}

/**
 * Upload a contract for review and hear each pipeline stage as it happens.
 *
 * The form is replayable (a FormData), so the one retry `authorizedFetch` makes after a
 * token refresh sends the same file again.
 */
export async function streamContractReview(
  form: FormData,
  onEvent: (event: ReviewStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await authorizedFetch(`${API_BASE}/api/v1/legal/review-document/stream`, {
    method: "POST",
    headers: { Accept: "text/event-stream" },
    body: form,
    signal,
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
    const detail = typeof body?.detail === "string" ? body.detail : `Không thể rà soát (${response.status})`;
    throw new Error(detail);
  }
  if (!response.body) throw new Error("Máy chủ không trả về luồng tiến trình.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done }).replaceAll("\r\n", "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const parsed = parseBlock(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + 2);
      if (parsed) onEvent(parsed);
      boundary = buffer.indexOf("\n\n");
    }
    if (done) break;
  }
  const last = parseBlock(buffer.trim());
  if (last) onEvent(last);
}
