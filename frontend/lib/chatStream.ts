export type ExecutionPhase =
  | "ANALYZING"
  | "SEARCHING"
  | "TOOL_CALLING"
  | "WAITING_APPROVAL"
  | "COMPLETED";

export type ChatStreamEvent =
  | { event: "status"; phase: ExecutionPhase }
  | { event: "token"; delta: string }
  | { event: "complete"; [key: string]: unknown }
  | { event: "error"; message: string };

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

function parseEventBlock(block: string): ChatStreamEvent | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }
  if (!dataLines.length) return null;
  const data = JSON.parse(dataLines.join("\n")) as Record<string, unknown>;
  return { event, ...data } as ChatStreamEvent;
}

export async function streamAgentChat(
  payload: Record<string, unknown>,
  onEvent: (event: ChatStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const token = typeof window !== "undefined"
    ? window.localStorage.getItem("access_token")
    : null;
  const response = await fetch(`${API_BASE}/api/v1/agent/chat/stream`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(payload),
    signal,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null;
    throw new Error(body?.detail || `Streaming request failed (${response.status})`);
  }
  if (!response.body) throw new Error("Streaming response has no body");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done }).replaceAll("\r\n", "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const event = parseEventBlock(block);
      if (event) onEvent(event);
      boundary = buffer.indexOf("\n\n");
    }
    if (done) break;
  }
  const finalEvent = parseEventBlock(buffer.trim());
  if (finalEvent) onEvent(finalEvent);
}
