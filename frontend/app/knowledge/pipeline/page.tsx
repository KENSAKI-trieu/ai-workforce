"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import axios from "axios";
import { Check, CheckCircle2, Circle, FileText, Loader2, RotateCcw, X, XCircle } from "lucide-react";

import api from "@/lib/api";
import { useAuthStore } from "@/store/useAuthStore";
import { useKnowledgeWorkflowStore } from "@/store/useKnowledgeWorkflowStore";
import KnowledgeShell from "../_components/KnowledgeShell";
import WizardHeader from "../_components/WizardHeader";
import type { AIProcessingProgress, PipelineStage, ProcessingStatus } from "../_lib/types";
import { formatFileSize, messageFrom, processingMessage } from "../_lib/utils";
import styles from "../knowledge.module.css";

const stages: Array<{ key: Exclude<PipelineStage, "failed">; label: string; description: string }> = [
  { key: "uploading", label: "Tải tài liệu lên", description: "Truyền file an toàn tới máy chủ" },
  { key: "parsing", label: "Đọc và làm sạch nội dung", description: "Trích xuất văn bản từ tài liệu" },
  { key: "chunking", label: "Chia chunk", description: "Áp dụng cấu hình token và overlap đã chọn" },
  { key: "embedding", label: "Tạo embedding", description: "Chuyển chunk thành vector tìm kiếm" },
  { key: "indexing", label: "Lập chỉ mục", description: "Lưu dữ liệu vào kho tri thức" },
  { key: "ready", label: "Hoàn tất", description: "Tài liệu sẵn sàng cho RAG" },
];

const rank: Record<PipelineStage, number> = { uploading: 0, parsing: 1, chunking: 2, embedding: 3, indexing: 4, ready: 5, failed: 0 };
const AI_SERVICE_BASE = process.env.NEXT_PUBLIC_AI_SERVICE_URL || "http://localhost:8100";

interface DuplicateConflict {
  code: "DUPLICATE_CHUNKS";
  message: string;
  duplicate_count: number;
}

export default function KnowledgePipelinePage() {
  const router = useRouter();
  const { hasHydrated, isAuthenticated } = useAuthStore();
  const [stage, setStage] = useState<PipelineStage>("uploading");
  const [failedStage, setFailedStage] = useState<Exclude<PipelineStage, "failed"> | null>(null);
  const [progress, setProgress] = useState(0);
  const [processingStatus, setProcessingStatus] = useState<ProcessingStatus | null>(null);
  const [chunkCount, setChunkCount] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [duplicate, setDuplicate] = useState<DuplicateConflict | null>(null);
  const [busy, setBusy] = useState(false);
  const workflowFile = useKnowledgeWorkflowStore((state) => state.file);
  const workflowCollection = useKnowledgeWorkflowStore((state) => state.collection);
  const workflowDepartment = useKnowledgeWorkflowStore((state) => state.department);
  const workflowConfig = useKnowledgeWorkflowStore((state) => state.config);
  const workflowDocumentId = useKnowledgeWorkflowStore((state) => state.documentId);
  const workflowVersion = useKnowledgeWorkflowStore((state) => state.version);
  const setUploadedDocument = useKnowledgeWorkflowStore((state) => state.setUploadedDocument);
  const resetWorkflow = useKnowledgeWorkflowStore((state) => state.reset);
  const started = useRef(false);
  const pollingGeneration = useRef(0);
  const eventStreamAbort = useRef<AbortController | null>(null);
  const aiEventStreamAbort = useRef<AbortController | null>(null);
  const latestStageRank = useRef(0);
  const directAIProgressSeen = useRef(false);

  const applyStatus = useCallback((data: ProcessingStatus): boolean => {
    const nextStage = data.processing_status === "uploaded" ? "parsing" : data.processing_status;
    if (nextStage !== "failed" && rank[nextStage] < latestStageRank.current) {
      return false;
    }
    if (
      directAIProgressSeen.current
      && (nextStage === "chunking" || nextStage === "embedding")
      && rank[nextStage] === latestStageRank.current
    ) {
      return false;
    }
    if (nextStage !== "failed") latestStageRank.current = rank[nextStage];
    setProcessingStatus(data);
    setProgress(data.processing_progress);
    setChunkCount(data.chunks_created ?? data.chunk_count);
    if (nextStage === "failed") {
      const failedAt = data.failed_stage || "uploading";
      const failedLabel = stages.find((item) => item.key === failedAt)?.label || failedAt;
      setFailedStage(failedAt);
      setStage("failed");
      setError(`${failedLabel}: ${processingMessage(data.error_message)}`);
      console.error("[Knowledge pipeline failed]", {
        documentId: data.document_id,
        stage: failedAt,
        checkpoint: data.processing_checkpoint,
        error: data.error_message,
        updatedAt: data.updated_at,
      });
      return true;
    }
    setFailedStage(null);
    setStage(nextStage);
    return nextStage === "ready";
  }, []);

  const applyAIProgress = useCallback((data: AIProcessingProgress) => {
    if (rank[data.processing_status] < latestStageRank.current) return;
    latestStageRank.current = rank[data.processing_status];
    directAIProgressSeen.current = true;
    setStage(data.processing_status);
    setProgress(data.processing_progress);
    if (data.chunks_created != null) setChunkCount(data.chunks_created);
    setProcessingStatus((current) => {
      if (!current) return current;
      return {
        ...current,
        ...data,
        chunk_count: data.chunks_created ?? current.chunk_count,
      };
    });
  }, []);

  const pollStatus = useCallback(async (
    documentId: string,
    version: string,
    generation: number,
  ) => {
    while (pollingGeneration.current === generation) {
      try {
        const { data } = await api.get<ProcessingStatus>(`/api/v1/documents/processing-status/${encodeURIComponent(documentId)}`, { params: { version }, timeout: 5000 });
        if (pollingGeneration.current !== generation) return;
        if (applyStatus(data)) return;
      } catch {
        // The durable document record may not exist until the upload reaches the server.
      }
      await new Promise((resolve) => window.setTimeout(resolve, 250));
    }
  }, [applyStatus]);

  const streamStatus = useCallback(async (
    documentId: string,
    version: string,
    generation: number,
  ) => {
    const controller = new AbortController();
    eventStreamAbort.current?.abort();
    eventStreamAbort.current = controller;
    try {
      const baseUrl = api.defaults.baseURL || window.location.origin;
      const url = new URL(
        `/api/v1/documents/processing-events/${encodeURIComponent(documentId)}`,
        baseUrl,
      );
      url.searchParams.set("version", version);
      const token = localStorage.getItem("access_token");
      const response = await fetch(url, {
        credentials: "include",
        headers: {
          Accept: "text/event-stream",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        signal: controller.signal,
      });
      if (!response.ok || !response.body) {
        throw new Error(`Pipeline event stream returned HTTP ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (pollingGeneration.current === generation) {
        const { done, value } = await reader.read();
        if (done) return;
        buffer += decoder.decode(value, { stream: true });
        const blocks = buffer.split(/\r?\n\r?\n/);
        buffer = blocks.pop() || "";
        for (const block of blocks) {
          const dataText = block
            .split(/\r?\n/)
            .filter((line) => line.startsWith("data:"))
            .map((line) => line.slice(5).trimStart())
            .join("\n");
          if (!dataText) continue;
          const data = JSON.parse(dataText) as ProcessingStatus | { code: string; message: string };
          if ("processing_status" in data) {
            if (applyStatus(data)) return;
            // A single network read may contain several fast backend events.
            // Yield one paint so React renders every real AI response instead
            // of batching the entire pipeline into the terminal 100% state.
            await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
          } else {
            throw new Error(`${data.code}: ${data.message}`);
          }
        }
      }
    } catch (reason) {
      if (controller.signal.aborted || pollingGeneration.current !== generation) return;
      console.warn("[Knowledge pipeline stream disconnected; falling back to polling]", {
        documentId,
        error: reason instanceof Error ? reason.message : String(reason),
      });
      void pollStatus(documentId, version, generation);
    }
  }, [applyStatus, pollStatus]);

  const streamAIStatus = useCallback(async (
    processingStreamId: string,
    generation: number,
  ) => {
    const controller = new AbortController();
    aiEventStreamAbort.current?.abort();
    aiEventStreamAbort.current = controller;
    try {
      const url = new URL(
        `/v1/pipeline/events/${encodeURIComponent(processingStreamId)}`,
        AI_SERVICE_BASE,
      );
      const response = await fetch(url, {
        headers: { Accept: "text/event-stream" },
        signal: controller.signal,
      });
      if (!response.ok || !response.body) {
        throw new Error(`AI pipeline event stream returned HTTP ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let receivedTerminalEvent = false;
      while (pollingGeneration.current === generation) {
        const { done, value } = await reader.read();
        if (done) {
          if (!receivedTerminalEvent) directAIProgressSeen.current = false;
          return;
        }
        buffer += decoder.decode(value, { stream: true });
        const blocks = buffer.split(/\r?\n\r?\n/);
        buffer = blocks.pop() || "";
        for (const block of blocks) {
          const dataText = block
            .split(/\r?\n/)
            .filter((line) => line.startsWith("data:"))
            .map((line) => line.slice(5).trimStart())
            .join("\n");
          if (!dataText) continue;
          const data = JSON.parse(dataText) as AIProcessingProgress;
          applyAIProgress(data);
          receivedTerminalEvent = (
            data.processing_status === "embedding"
            && data.embedding_remaining_chunks === 0
          );
          await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
        }
      }
    } catch (reason) {
      if (controller.signal.aborted || pollingGeneration.current !== generation) return;
      directAIProgressSeen.current = false;
      console.warn("[Knowledge pipeline] direct AI stream disconnected", {
        processingStreamId,
        error: reason instanceof Error ? reason.message : String(reason),
      });
    }
  }, [applyAIProgress]);

  const upload = useCallback(async (duplicateStrategy: "prompt" | "replace" | "keep_old") => {
    if (!workflowFile) return;
    setBusy(true);
    setError(null);
    setDuplicate(null);
    setFailedStage(null);
    setStage("uploading");
    setProcessingStatus(null);
    setProgress(0);
    latestStageRank.current = 0;
    directAIProgressSeen.current = false;
    eventStreamAbort.current?.abort();
    aiEventStreamAbort.current?.abort();
    pollingGeneration.current += 1;
    try {
      const body = new FormData();
      body.append("file", workflowFile);
      body.append("collection_name", workflowCollection);
      body.append("department_access", workflowDepartment);
      body.append("duplicate_strategy", duplicateStrategy);
      body.append("chunking_mode", workflowConfig.mode);
      body.append("chunk_size", String(workflowConfig.chunk_size));
      body.append("chunk_overlap", String(workflowConfig.chunk_overlap));
      body.append("parent_chunk_size", String(workflowConfig.parent_chunk_size));
      body.append("async_processing", "true");
      const { data } = await api.post<{
        document_id: string;
        version?: string;
        chunks_created?: number;
        processing_status?: ProcessingStatus["processing_status"];
        processing_progress?: number;
        processing_stream_id?: string;
      }>("/api/v1/documents/upload", body, {
        timeout: 60000,
        onUploadProgress: (event) => {
          const total = event.total || workflowFile.size || 1;
          setProgress(Math.min(100, Math.round((event.loaded / total) * 100)));
        },
      });
      setUploadedDocument(data.document_id, data.version || "1.0");
      setChunkCount(data.chunks_created ?? null);
      if (data.processing_status === "ready") {
        pollingGeneration.current += 1;
        setStage("ready");
        setProgress(100);
      } else {
        const statusGeneration = ++pollingGeneration.current;
        const initialStage = data.processing_status === "uploaded" || !data.processing_status ? "parsing" : data.processing_status;
        latestStageRank.current = rank[initialStage];
        setStage(initialStage);
        setProgress(data.processing_progress ?? 0);
        setProcessingStatus({
          document_id: data.document_id,
          document_name: workflowFile.name,
          version: data.version || "1.0",
          processing_status: data.processing_status || "uploaded",
          processing_progress: data.processing_progress ?? 0,
          chunk_count: data.chunks_created ?? 0,
          error_message: null,
        });
        if (data.processing_stream_id) {
          void streamAIStatus(data.processing_stream_id, statusGeneration);
        }
        void streamStatus(data.document_id, data.version || "1.0", statusGeneration);
      }
    } catch (reason) {
      pollingGeneration.current += 1;
      if (axios.isAxiosError(reason) && reason.response?.status === 409) {
        const detail = (reason.response.data as { detail?: DuplicateConflict }).detail;
        if (detail?.code === "DUPLICATE_CHUNKS") {
          setDuplicate(detail);
          setStage("failed");
          return;
        }
      }
      setStage("failed");
      setError(messageFrom(reason));
    } finally {
      setBusy(false);
    }
  }, [
    setUploadedDocument,
    streamAIStatus,
    streamStatus,
    workflowCollection,
    workflowConfig,
    workflowDepartment,
    workflowFile,
  ]);

  useEffect(() => {
    if (!hasHydrated) return;
    if (!isAuthenticated) return;
    if (!workflowFile) {
      router.replace("/knowledge/new");
      return;
    }
    if (started.current) return;
    started.current = true;
    void upload("prompt");
    return () => {
      pollingGeneration.current += 1;
      eventStreamAbort.current?.abort();
      aiEventStreamAbort.current?.abort();
    };
  }, [hasHydrated, isAuthenticated, router, upload, workflowFile]);

  const openDocument = () => {
    if (!workflowDocumentId) return;
    const href = `/knowledge/document?documentId=${encodeURIComponent(workflowDocumentId)}&version=${encodeURIComponent(workflowVersion)}`;
    resetWorkflow();
    router.push(href);
  };

  if (!workflowFile) return null;
  const failed = stage === "failed";
  const ready = stage === "ready";
  const currentRank = failed ? rank[failedStage || "uploading"] : rank[stage];

  return (
    <KnowledgeShell>
      <WizardHeader current={3} />
      <main className={styles.pipelineWrap}>
        <div className={styles.wizardTitle}>
          <h1>{ready ? "Tài liệu đã sẵn sàng" : failed ? "Cần xử lý thêm" : "Đang xử lý tài liệu"}</h1>
          <p>Bạn có thể theo dõi từng bước chunk, embedding và lập chỉ mục tại đây.</p>
        </div>
        <section className={`${styles.pipelineCard} ta-card`}>
          <div className={styles.pipelineFile}>
            <span className={styles.fileIcon}><FileText size={22} /></span>
            <div><strong>{workflowFile.name}</strong><small>{formatFileSize(workflowFile.size)} · {workflowConfig.mode === "parent_child" ? "Cha – con" : "Từng đoạn"}</small></div>
            {chunkCount != null && <span className="ta-badge ta-badge-success">{chunkCount} chunks</span>}
          </div>

          <div className={styles.pipelineSteps} aria-live="polite">
            {stages.map((item, index) => {
              const complete = ready || index < currentRank;
              const active = !ready && !failed && index === currentRank;
              const itemFailed = failed && index === currentRank;
              const percent = complete ? 100 : active ? progress : 0;
              let description = item.description;
              if (
                active
                && item.key === "chunking"
                && processingStatus?.chunk_segments_total != null
              ) {
                description = `Đã xử lý ${processingStatus.chunk_segments_processed ?? 0}/${processingStatus.chunk_segments_total} đoạn · còn ${processingStatus.chunk_segments_remaining ?? 0} đoạn · tạo ${processingStatus.chunks_created ?? 0} chunk`;
              }
              if (
                active
                && item.key === "embedding"
                && processingStatus?.embedding_total_chunks != null
              ) {
                description = `Đã embedding ${processingStatus.embedded_chunks ?? 0}/${processingStatus.embedding_total_chunks} chunk · còn ${processingStatus.embedding_remaining_chunks ?? 0} chunk`;
              }
              return (
                <div className={styles.pipelineStep} key={item.key}>
                  <div className={styles.pipelineRail}>
                    <span className={`${styles.pipelineIcon} ${complete ? styles.pipelineComplete : active ? styles.pipelineActive : itemFailed ? styles.pipelineFailed : ""}`}>
                      {complete ? <Check size={13} /> : active ? <Loader2 className="animate-spin" size={13} /> : itemFailed ? <X size={13} /> : <Circle size={9} />}
                    </span>
                  </div>
                  <div className={styles.pipelineBody}>
                    <header><strong>{item.label}</strong><small>{percent}%</small></header>
                    <small>{description}</small>
                    <div className={styles.progress}><span style={{ width: `${percent}%` }} /></div>
                  </div>
                </div>
              );
            })}
          </div>

          {error && <div className={styles.error}><XCircle size={16} style={{ verticalAlign: "middle", marginRight: 7 }} />{error}</div>}
          {duplicate && (
            <div className={styles.error}>
              Phát hiện {duplicate.duplicate_count} chunk trùng với tài liệu hiện có.
              <div className={styles.actions}>
                <button type="button" className="ta-btn ta-btn-ghost" disabled={busy} onClick={() => void upload("keep_old")}>Giữ bản cũ</button>
                <button type="button" className="ta-btn ta-btn-primary" disabled={busy} onClick={() => void upload("replace")}>Thay thế và xử lý</button>
              </div>
            </div>
          )}
          {ready && <div className={styles.successBox}><CheckCircle2 size={20} /> Tài liệu đã được chunk và embedding thành công.</div>}

          <div className={styles.actions}>
            {failed && !duplicate && <button type="button" className="ta-btn ta-btn-ghost" disabled={busy} onClick={() => void upload("prompt")}><RotateCcw size={15} /> Thử lại</button>}
            {ready && <button type="button" className="ta-btn ta-btn-primary" onClick={openDocument}>Done</button>}
          </div>
        </section>
      </main>
    </KnowledgeShell>
  );
}
