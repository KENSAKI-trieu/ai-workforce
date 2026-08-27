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
import type { PipelineStage, ProcessingStatus } from "../_lib/types";
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

interface DuplicateConflict {
  code: "DUPLICATE_CHUNKS";
  message: string;
  duplicate_count: number;
}

export default function KnowledgePipelinePage() {
  const router = useRouter();
  const { hasHydrated, isAuthenticated } = useAuthStore();
  const workflow = useKnowledgeWorkflowStore();
  const [stage, setStage] = useState<PipelineStage>("uploading");
  const [failedStage, setFailedStage] = useState<Exclude<PipelineStage, "failed"> | null>(null);
  const [progress, setProgress] = useState(0);
  const [chunkCount, setChunkCount] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [duplicate, setDuplicate] = useState<DuplicateConflict | null>(null);
  const [busy, setBusy] = useState(false);
  const started = useRef(false);
  const pollingGeneration = useRef(0);
  const eventStreamAbort = useRef<AbortController | null>(null);

  const applyStatus = useCallback((data: ProcessingStatus): boolean => {
    const nextStage = data.processing_status === "uploaded" ? "parsing" : data.processing_status;
    setProgress(data.processing_progress);
    setChunkCount(data.chunk_count);
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

  const pollStatus = useCallback(async (documentId: string, generation: number) => {
    while (pollingGeneration.current === generation) {
      try {
        const { data } = await api.get<ProcessingStatus>(`/api/v1/documents/processing-status/${encodeURIComponent(documentId)}`, { params: { version: "1.0" }, timeout: 5000 });
        if (pollingGeneration.current !== generation) return;
        if (applyStatus(data)) return;
      } catch {
        // The durable document record may not exist until the upload reaches the server.
      }
      await new Promise((resolve) => window.setTimeout(resolve, 650));
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
      void pollStatus(documentId, generation);
    }
  }, [applyStatus, pollStatus]);

  const upload = useCallback(async (duplicateStrategy: "prompt" | "replace" | "keep_old") => {
    if (!workflow.file) return;
    setBusy(true);
    setError(null);
    setDuplicate(null);
    setFailedStage(null);
    setStage("uploading");
    setProgress(0);
    eventStreamAbort.current?.abort();
    pollingGeneration.current += 1;
    try {
      const body = new FormData();
      body.append("file", workflow.file);
      body.append("collection_name", workflow.collection);
      body.append("department_access", workflow.department);
      body.append("duplicate_strategy", duplicateStrategy);
      body.append("chunking_mode", workflow.config.mode);
      body.append("chunk_size", String(workflow.config.chunk_size));
      body.append("chunk_overlap", String(workflow.config.chunk_overlap));
      body.append("parent_chunk_size", String(workflow.config.parent_chunk_size));
      body.append("async_processing", "true");
      const { data } = await api.post<{
        document_id: string;
        version?: string;
        chunks_created?: number;
        processing_status?: ProcessingStatus["processing_status"];
        processing_progress?: number;
      }>("/api/v1/documents/upload", body, {
        timeout: 60000,
        onUploadProgress: (event) => {
          const total = event.total || workflow.file?.size || 1;
          setProgress(Math.min(100, Math.round((event.loaded / total) * 100)));
        },
      });
      workflow.setUploadedDocument(data.document_id, data.version || "1.0");
      setChunkCount(data.chunks_created ?? null);
      if (data.processing_status === "ready") {
        pollingGeneration.current += 1;
        setStage("ready");
        setProgress(100);
      } else {
        const statusGeneration = ++pollingGeneration.current;
        setStage(data.processing_status === "uploaded" || !data.processing_status ? "parsing" : data.processing_status);
        setProgress(data.processing_progress ?? 0);
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
  }, [streamStatus, workflow]);

  useEffect(() => {
    if (!hasHydrated) return;
    if (!isAuthenticated) return;
    if (!workflow.file) {
      router.replace("/knowledge/new");
      return;
    }
    if (started.current) return;
    started.current = true;
    void upload("prompt");
    return () => {
      pollingGeneration.current += 1;
      eventStreamAbort.current?.abort();
    };
  }, [hasHydrated, isAuthenticated, router, upload, workflow.file]);

  const openDocument = () => {
    if (!workflow.documentId) return;
    const href = `/knowledge/document?documentId=${encodeURIComponent(workflow.documentId)}&version=${encodeURIComponent(workflow.version)}`;
    workflow.reset();
    router.push(href);
  };

  if (!workflow.file) return null;
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
            <div><strong>{workflow.file.name}</strong><small>{formatFileSize(workflow.file.size)} · {workflow.config.mode === "parent_child" ? "Cha – con" : "Từng đoạn"}</small></div>
            {chunkCount != null && <span className="ta-badge ta-badge-success">{chunkCount} chunks</span>}
          </div>

          <div className={styles.pipelineSteps} aria-live="polite">
            {stages.map((item, index) => {
              const complete = ready || index < currentRank;
              const active = !ready && !failed && index === currentRank;
              const itemFailed = failed && index === currentRank;
              const percent = complete ? 100 : active ? progress : 0;
              return (
                <div className={styles.pipelineStep} key={item.key}>
                  <div className={styles.pipelineRail}>
                    <span className={`${styles.pipelineIcon} ${complete ? styles.pipelineComplete : active ? styles.pipelineActive : itemFailed ? styles.pipelineFailed : ""}`}>
                      {complete ? <Check size={13} /> : active ? <Loader2 className="animate-spin" size={13} /> : itemFailed ? <X size={13} /> : <Circle size={9} />}
                    </span>
                  </div>
                  <div className={styles.pipelineBody}>
                    <header><strong>{item.label}</strong><small>{percent}%</small></header>
                    <small>{item.description}</small>
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
