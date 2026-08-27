"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { BookOpen, Boxes, Clock3, FileText, Loader2, Plus, Search, TestTube2, Trash2 } from "lucide-react";

import api from "@/lib/api";
import { useAuthStore } from "@/store/useAuthStore";
import KnowledgeShell from "./_components/KnowledgeShell";
import DeleteDocumentDialog from "./_components/DeleteDocumentDialog";
import RetrievalTestDialog from "./_components/RetrievalTestDialog";
import type { KnowledgeDocument } from "./_lib/types";
import { formatDate, messageFrom, processingMessage } from "./_lib/utils";
import styles from "./knowledge.module.css";

const statusLabel: Record<string, string> = {
  ready: "Sẵn sàng",
  uploaded: "Đã tải lên",
  parsing: "Đang đọc",
  chunking: "Đang chia đoạn",
  embedding: "Đang embedding",
  indexing: "Đang lập chỉ mục",
  failed: "Thất bại",
};

export default function KnowledgePage() {
  const router = useRouter();
  const { hasHydrated, isAuthenticated } = useAuthStore();
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retrievalOpen, setRetrievalOpen] = useState(false);
  const [documentToDelete, setDocumentToDelete] = useState<KnowledgeDocument | null>(null);
  const [deletePending, setDeletePending] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const fetchDocuments = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    setError(null);
    try {
      const { data } = await api.get<KnowledgeDocument[]>("/api/v1/documents", { params: { mine: true } });
      setDocuments(data);
    } catch (reason) {
      setError(messageFrom(reason));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!hasHydrated || !isAuthenticated) return;
    const timer = window.setTimeout(() => void fetchDocuments(false), 0);
    return () => window.clearTimeout(timer);
  }, [fetchDocuments, hasHydrated, isAuthenticated]);

  useEffect(() => {
    if (!isAuthenticated || !documents.some((item) => item.processing_status && !["ready", "failed"].includes(item.processing_status))) return;
    const timer = window.setInterval(() => void fetchDocuments(true), 1800);
    return () => window.clearInterval(timer);
  }, [documents, fetchDocuments, isAuthenticated]);

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return documents;
    return documents.filter((item) =>
      `${item.document_name} ${item.collection_name} ${item.department_access}`.toLowerCase().includes(query)
    );
  }, [documents, search]);

  const closeDeleteDialog = useCallback(() => {
    if (deletePending) return;
    setDocumentToDelete(null);
    setDeleteError(null);
  }, [deletePending]);

  const deleteDocument = useCallback(async () => {
    if (!documentToDelete || deletePending) return;
    setDeletePending(true);
    setDeleteError(null);
    try {
      await api.delete(`/api/v1/documents/${encodeURIComponent(documentToDelete.document_id)}`, {
        params: { version: documentToDelete.version },
      });
      setDocuments((current) => current.filter((item) => !(
        item.document_id === documentToDelete.document_id && item.version === documentToDelete.version
      )));
      setDocumentToDelete(null);
    } catch (reason) {
      setDeleteError(messageFrom(reason));
    } finally {
      setDeletePending(false);
    }
  }, [deletePending, documentToDelete]);

  return (
    <KnowledgeShell>
      <header className={styles.topbar}>
        <div className={styles.breadcrumb}><span>Trang chủ</span><span>›</span><strong>Kiến thức</strong></div>
        <span className="ta-badge ta-badge-success">RAG đang hoạt động</span>
      </header>
      <main className={styles.main}>
        <div className={styles.headingRow}>
          <div>
            <h1>Tài liệu của tôi</h1>
            <p>Xem các file bạn đã tải lên, trạng thái xử lý và các chunk đã lập chỉ mục.</p>
          </div>
          <div className={styles.headingActions}>
            <button type="button" className="ta-btn ta-btn-ghost" onClick={() => setRetrievalOpen(true)}>
              <TestTube2 size={17} /> Test Retrieval
            </button>
            <button type="button" className="ta-btn ta-btn-primary" onClick={() => router.push("/knowledge/new")}>
              <Plus size={17} /> Tạo mới
            </button>
          </div>
        </div>

        <div className={styles.toolbar}>
          <label className={styles.search}>
            <Search size={17} />
            <input className="ta-input" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Tìm kiếm tài liệu" />
          </label>
          <span className="ta-badge ta-badge-neutral">{filtered.length} tài liệu</span>
        </div>

        {error && <div className={styles.error}>{error}</div>}
        {loading ? (
          <div className={styles.loading}><Loader2 className="animate-spin" size={19} /> Đang tải tài liệu...</div>
        ) : filtered.length ? (
          <section className={styles.documentGrid}>
            {filtered.map((item) => {
              const status = item.processing_status || "ready";
              const active = !["ready", "failed"].includes(status);
              return (
                <article
                  key={`${item.document_id}:${item.version}`}
                  className={`${styles.documentCard} ta-card`}
                >
                  <button
                    type="button"
                    className={styles.documentOpen}
                    aria-label={`Mở tài liệu ${item.document_name}`}
                    onClick={() => router.push(`/knowledge/document?documentId=${encodeURIComponent(item.document_id)}&version=${encodeURIComponent(item.version)}`)}
                  >
                    <span className={styles.documentHead}>
                      <span className={styles.fileIcon}>{active ? <Loader2 className="animate-spin" size={22} /> : <FileText size={22} />}</span>
                      <span className={styles.documentTitle}>
                        <strong>{item.document_name}</strong>
                        <small>{item.collection_name} · {item.department_access}</small>
                      </span>
                      <span className={`ta-badge ${status === "failed" ? "ta-badge-danger" : status === "ready" ? "ta-badge-success" : "ta-badge-info"}`}>
                        {statusLabel[status] || item.status}
                      </span>
                    </span>
                    <span className={styles.documentDescription}>
                      {item.error_message ? processingMessage(item.error_message) : "Tài liệu sẵn sàng để xem trước nội dung và kiểm tra từng chunk đã được tạo."}
                    </span>
                    <span className={styles.documentFooter}>
                      <span className={styles.metrics}>
                        <span className={styles.metric}><Boxes size={14} /> {item.chunk_count} chunks</span>
                        {active && <span>{item.processing_progress ?? 0}%</span>}
                      </span>
                      <span className={styles.metric}><Clock3 size={14} /> {formatDate(item.created_at)}</span>
                    </span>
                  </button>
                  <button
                    type="button"
                    className={styles.documentDelete}
                    aria-label={`Xóa tài liệu ${item.document_name}`}
                    title="Xóa tài liệu"
                    onClick={() => {
                      setDeleteError(null);
                      setDocumentToDelete(item);
                    }}
                  >
                    <Trash2 size={16} />
                  </button>
                </article>
              );
            })}
          </section>
        ) : (
          <section className={`${styles.empty} ta-card`}>
            <BookOpen size={34} color="var(--primary)" />
            <strong>Chưa có tài liệu nào</strong>
            <span>Tạo tài liệu đầu tiên để bắt đầu xây dựng kho kiến thức.</span>
            <button type="button" className="ta-btn ta-btn-primary" onClick={() => router.push("/knowledge/new")}><Plus size={16} /> Tạo mới</button>
          </section>
        )}
      </main>
      <RetrievalTestDialog open={retrievalOpen} onClose={() => setRetrievalOpen(false)} />
      <DeleteDocumentDialog
        document={documentToDelete}
        pending={deletePending}
        error={deleteError}
        onClose={closeDeleteDialog}
        onConfirm={() => void deleteDocument()}
      />
    </KnowledgeShell>
  );
}
