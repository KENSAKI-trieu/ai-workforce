"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Boxes, Check, Circle, Download, FileText, Layers3, Loader2, Search, X } from "lucide-react";

import api from "@/lib/api";
import { useAuthStore } from "@/store/useAuthStore";
import KnowledgeShell from "../_components/KnowledgeShell";
import type { DocumentReader } from "../_lib/types";
import { messageFrom } from "../_lib/utils";
import styles from "../knowledge.module.css";

const detailPipeline = [
  { key: "uploading", label: "Tải lên" },
  { key: "parsing", label: "Đọc nội dung" },
  { key: "chunking", label: "Chia chunk" },
  { key: "embedding", label: "Tạo embedding" },
  { key: "indexing", label: "Lập chỉ mục" },
  { key: "ready", label: "Hoàn tất" },
] as const;

const detailRank: Record<string, number> = { uploaded: 1, parsing: 1, chunking: 2, embedding: 3, indexing: 4, ready: 5, failed: 0 };
const checkpointRank: Record<string, number> = { uploaded: 1, parsed: 2, chunked: 3, embedded: 4, ready: 5 };

export default function DocumentDetailClient({ documentId, version }: { documentId: string; version: string }) {
  const { hasHydrated, isAuthenticated } = useAuthStore();
  const [reader, setReader] = useState<DocumentReader | null>(null);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(Boolean(documentId));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!documentId || !hasHydrated || !isAuthenticated) return;
    api.get<DocumentReader>(`/api/v1/documents/${encodeURIComponent(documentId)}/reader`, { params: { version } })
      .then(({ data }) => setReader(data))
      .catch((reason) => setError(messageFrom(reason)))
      .finally(() => setLoading(false));
  }, [documentId, hasHydrated, isAuthenticated, version]);

  const displayedError = documentId ? error : "Thiếu mã tài liệu.";
  const processingStage = reader?.processing_status || "ready";
  const currentRank = processingStage === "failed"
    ? checkpointRank[reader?.processing_checkpoint || "uploaded"] ?? 0
    : detailRank[processingStage] ?? 0;

  const chunks = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return reader?.chunks || [];
    return (reader?.chunks || []).filter((chunk) => `${chunk.section_title} ${chunk.content}`.toLowerCase().includes(query));
  }, [reader, search]);

  const downloadOriginal = async () => {
    if (!reader?.download_url) return;
    try {
      const response = await api.get<Blob>(reader.download_url, { responseType: "blob" });
      const href = URL.createObjectURL(response.data);
      const anchor = document.createElement("a");
      anchor.href = href;
      anchor.download = reader.document_name;
      anchor.click();
      URL.revokeObjectURL(href);
    } catch (reason) {
      setError(messageFrom(reason));
    }
  };

  return (
    <KnowledgeShell>
      <header className={styles.topbar}>
        <div className={styles.breadcrumb}><Link className={styles.backLink} href="/knowledge"><ArrowLeft size={16} /> Kiến thức</Link><span>›</span><strong>Xem chunk</strong></div>
        {reader?.download_url && <button type="button" className="ta-btn ta-btn-ghost" onClick={() => void downloadOriginal()}><Download size={15} /> Tải file gốc</button>}
      </header>
      <main className={styles.main}>
        {displayedError && <div className={styles.error}>{displayedError}</div>}
        {loading ? <div className={styles.loading}><Loader2 className="animate-spin" size={19} /> Đang đọc tài liệu...</div> : reader && (
          <div className={styles.detailGrid}>
            <aside className={`${styles.detailSidebar} ta-card`}>
              <span className={styles.fileIcon}><FileText size={23} /></span>
              <h2>{reader.document_title || reader.document_name}</h2>
              <span className={`ta-badge ${processingStage === "failed" ? "ta-badge-danger" : processingStage === "ready" ? "ta-badge-success" : "ta-badge-info"}`}>
                {processingStage === "failed" ? "Thất bại" : processingStage === "ready" ? "Sẵn sàng" : "Đang xử lý"}
              </span>
              <div className={styles.metaList}>
                <Meta label="Tên file" value={reader.document_name} />
                <Meta label="Phiên bản" value={reader.version} />
                <Meta label="Số chunk" value={String(reader.chunk_count)} />
                <Meta label="Số ký tự" value={reader.character_count.toLocaleString("vi-VN")} />
                <Meta label="Kiểu phân đoạn" value={reader.chunking_config?.mode === "parent_child" ? "Cha – con" : "Từng đoạn"} />
                <Meta label="Token / overlap" value={`${reader.chunking_config?.chunk_size ?? "—"} / ${reader.chunking_config?.chunk_overlap ?? "—"}`} />
              </div>
              <div className={styles.miniPipeline} aria-label="Pipeline xử lý tài liệu">
                <strong>Pipeline xử lý</strong>
                {detailPipeline.map((item, index) => {
                  const complete = processingStage === "ready" || index < currentRank;
                  const active = processingStage !== "ready" && processingStage !== "failed" && index === currentRank;
                  const failed = processingStage === "failed" && index === currentRank;
                  return (
                    <div className={styles.miniPipelineStep} key={item.key}>
                      <span className={`${styles.miniPipelineDot} ${complete ? styles.miniPipelineDone : active ? styles.miniPipelineActive : failed ? styles.miniPipelineFailed : ""}`}>
                        {complete ? <Check size={11} /> : active ? <Loader2 className="animate-spin" size={11} /> : failed ? <X size={11} /> : <Circle size={7} />}
                      </span>
                      <span>{item.label}</span>
                      <span>{complete ? "100%" : active ? `${reader.processing_progress ?? 0}%` : "0%"}</span>
                    </div>
                  );
                })}
              </div>
            </aside>
            <section className={`${styles.chunksPanel} ta-card`}>
              <div className={styles.chunksToolbar}>
                <h2><Boxes size={18} style={{ verticalAlign: "middle", marginRight: 7 }} />Các chunk đã lập chỉ mục</h2>
                <label className={styles.search}><Search size={16} /><input className="ta-input" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Tìm trong chunk" /></label>
              </div>
              <div className={styles.chunksList}>
                {chunks.map((chunk) => (
                  <article className={`${styles.chunkCard} ${styles.fullChunk}`} key={chunk.id || chunk.chunk_index}>
                    <div className={styles.chunkMeta}>
                      <span>{chunk.parent_chunk_index != null ? <><Layers3 size={12} /> Parent {chunk.parent_chunk_index + 1} · </> : null}Chunk {chunk.chunk_index + 1}</span>
                      <span>{chunk.token_count ?? "—"} token{chunk.page_start ? ` · trang ${chunk.page_start}${chunk.page_end && chunk.page_end !== chunk.page_start ? `–${chunk.page_end}` : ""}` : ""}</span>
                    </div>
                    <h3>{chunk.section_title || "Không có tiêu đề"}</h3>
                    {chunk.parent_content && <details className={styles.parentContext}><summary>Xem ngữ cảnh parent</summary><div style={{ marginTop: 7, whiteSpace: "pre-wrap" }}>{chunk.parent_content}</div></details>}
                    <p>{chunk.content}</p>
                  </article>
                ))}
                {!chunks.length && <div className={styles.empty}><Boxes size={28} /><span>Không tìm thấy chunk phù hợp.</span></div>}
              </div>
            </section>
          </div>
        )}
      </main>
    </KnowledgeShell>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return <div className={styles.metaItem}><span>{label}</span><strong>{value}</strong></div>;
}
