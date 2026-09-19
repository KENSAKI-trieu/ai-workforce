"use client";

import { useEffect, useState } from "react";

import api from "@/lib/api";
import { authorizedFetch } from "@/lib/authorizedFetch";
import type { ProcessingStatus } from "./types";

/**
 * Shared reader for `GET /api/v1/documents/processing-events/{id}`.
 *
 * The backend names events so a client can tell history from live activity:
 * snapshots already queued when the stream opened arrive as `replay`, anything
 * the pipeline produces afterwards arrives as `status`, and the run ends with
 * `ready` or `failed`. Callers pace themselves on that distinction — replaying
 * a backlog frame by frame makes a finished pipeline look like a live one.
 */
export type ProcessingStreamEventName =
  | "replay"
  | "status"
  | "ready"
  | "failed"
  | "error"
  | "message";

export interface ProcessingStreamError {
  code: string;
  message: string;
}

export interface ProcessingStreamEvent {
  name: ProcessingStreamEventName;
  status?: ProcessingStatus;
  error?: ProcessingStreamError;
}

const STAGE_COUNTERS: Array<keyof ProcessingStatus> = [
  "chunk_segments_processed",
  "chunk_segments_total",
  "chunk_segments_remaining",
  "chunks_created",
  "embedded_chunks",
  "embedding_total_chunks",
  "embedding_remaining_chunks",
  "embedding_batch_count",
];

/**
 * Fold a snapshot into the previous one instead of replacing it.
 *
 * The stream's database-polling fallback emits the base contract only, with no
 * per-stage counters. Replacing state wholesale would blank "Đã embedding
 * 12/40 chunk" mid-run; merging keeps the last counters the live queue sent.
 * Counters are dropped only when the stage itself changes, so one stage never
 * shows another stage's numbers.
 */
export function mergeProcessingStatus(
  previous: ProcessingStatus | null,
  next: ProcessingStatus,
): ProcessingStatus {
  if (!previous) return next;
  const merged: ProcessingStatus = { ...previous, ...next };
  if (previous.processing_status !== next.processing_status) {
    for (const key of STAGE_COUNTERS) {
      if (!(key in next)) delete (merged as unknown as Record<string, unknown>)[key];
    }
  }
  return merged;
}

export function processingEventsUrl(documentId: string, version: string): URL {
  const baseUrl = api.defaults.baseURL || window.location.origin;
  const url = new URL(
    `/api/v1/documents/processing-events/${encodeURIComponent(documentId)}`,
    baseUrl,
  );
  url.searchParams.set("version", version);
  return url;
}

function parseEventBlock(block: string): ProcessingStreamEvent | null {
  let name: ProcessingStreamEventName = "message";
  const dataLines: string[] = [];
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith("event:")) {
      name = line.slice(6).trim() as ProcessingStreamEventName;
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
  }
  // Keep-alive comments carry no data lines.
  if (!dataLines.length) return null;
  const payload = JSON.parse(dataLines.join("\n"));
  if ("processing_status" in payload) {
    return { name, status: payload as ProcessingStatus };
  }
  return { name: "error", error: payload as ProcessingStreamError };
}

export async function readProcessingEvents(
  documentId: string,
  version: string,
  {
    signal,
    onEvent,
    shouldContinue = () => true,
  }: {
    signal: AbortSignal;
    onEvent: (event: ProcessingStreamEvent) => void | Promise<void>;
    shouldContinue?: () => boolean;
  },
): Promise<void> {
  const response = await authorizedFetch(processingEventsUrl(documentId, version), {
    headers: { Accept: "text/event-stream" },
    signal,
  });
  if (!response.ok || !response.body) {
    throw new Error(`Pipeline event stream returned HTTP ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (shouldContinue()) {
    const { done, value } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() || "";
    for (const block of blocks) {
      const event = parseEventBlock(block);
      if (!event) continue;
      await onEvent(event);
      if (!shouldContinue()) return;
    }
  }
}

/**
 * Follow one document's ingestion until it reaches a terminal stage.
 *
 * Read-only consumers (the document detail page) want the current state, not
 * the pacing control the upload wizard needs, so this hook applies every event
 * as soon as it arrives.
 */
export function useProcessingStream(
  documentId: string | null,
  version: string,
  enabled: boolean,
): ProcessingStatus | null {
  const [status, setStatus] = useState<ProcessingStatus | null>(null);

  useEffect(() => {
    if (!documentId || !enabled) return;
    const controller = new AbortController();
    let active = true;

    void (async () => {
      try {
        await readProcessingEvents(documentId, version, {
          signal: controller.signal,
          shouldContinue: () => active,
          onEvent: (event) => {
            if (!event.status) return;
            setStatus((previous) => mergeProcessingStatus(previous, event.status!));
          },
        });
      } catch (reason) {
        if (controller.signal.aborted || !active) return;
        console.warn("[Knowledge] processing stream disconnected", {
          documentId,
          error: reason instanceof Error ? reason.message : String(reason),
        });
      }
    })();

    return () => {
      active = false;
      controller.abort();
    };
  }, [documentId, enabled, version]);

  return status;
}
