import { authorizedFetch } from "@/lib/authorizedFetch";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export type StageStatus = "pending" | "running" | "done" | "failed";

export interface StageEvent {
  stage: string;
  status: "running" | "done";
  detail?: string | null;
  elapsed_ms: number;
}

export type MarketingStreamEvent<T> =
  | { event: "progress"; data: StageEvent }
  | { event: "complete"; data: T }
  | { event: "error"; data: { message: string; status_code?: number } };

function parseBlock<T>(block: string): MarketingStreamEvent<T> | null {
  let event = "message";
  const lines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    if (line.startsWith("data:")) lines.push(line.slice(5).trimStart());
  }
  if (!lines.length) return null; // keep-alive comments carry no data
  return { event, data: JSON.parse(lines.join("\n")) } as MarketingStreamEvent<T>;
}

/**
 * POST a JSON body to a Marketing SSE endpoint and hear each pipeline stage as it runs:
 * the outline after a brief, the posts and their fact-check after an approved outline.
 */
export async function streamMarketing<T>(
  path: string,
  body: Record<string, unknown>,
  onEvent: (event: MarketingStreamEvent<T>) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await authorizedFetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { Accept: "text/event-stream", "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: unknown } | null;
    const detail = typeof payload?.detail === "string" ? payload.detail : `Không thể thực hiện (${response.status})`;
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
      const parsed = parseBlock<T>(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + 2);
      if (parsed) onEvent(parsed);
      boundary = buffer.indexOf("\n\n");
    }
    if (done) break;
  }
  const last = parseBlock<T>(buffer.trim());
  if (last) onEvent(last);
}
