"use client";

import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, FileText, Loader2, Search, TestTube2, X } from "lucide-react";

import api from "@/lib/api";
import type { RetrievalResult } from "../_lib/types";
import { messageFrom } from "../_lib/utils";
import styles from "../knowledge.module.css";

interface RetrievalTestDialogProps {
  open: boolean;
  onClose: () => void;
}

export default function RetrievalTestDialog({ open, onClose }: RetrievalTestDialogProps) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [topK, setTopK] = useState(5);
  const [results, setResults] = useState<RetrievalResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", closeOnEscape);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", closeOnEscape);
      document.body.style.overflow = "";
    };
  }, [onClose, open]);

  if (!open) return null;

  const testRetrieval = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const normalizedQuery = query.trim();
    if (!normalizedQuery || loading) return;
    setLoading(true);
    setError(null);
    try {
      const { data } = await api.post<RetrievalResult[]>("/api/v1/documents/search", {
        query: normalizedQuery,
        top_k: topK,
      });
      setResults(data);
      setSearched(true);
    } catch (reason) {
      setError(messageFrom(reason));
      setResults([]);
      setSearched(true);
    } finally {
      setLoading(false);
    }
  };

  const openDocument = (result: RetrievalResult) => {
    onClose();
    router.push(`/knowledge/document?documentId=${encodeURIComponent(result.document_id)}&version=${encodeURIComponent(result.version)}`);
  };

  return (
    <div className={styles.retrievalBackdrop} role="presentation" onMouseDown={onClose}>
      <section
        aria-labelledby="retrieval-test-title"
        aria-modal="true"
        className={styles.retrievalDialog}
        role="dialog"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className={styles.retrievalHeader}>
          <div>
            <span className={styles.retrievalIcon}><TestTube2 size={20} /></span>
            <div>
              <h2 id="retrieval-test-title">Test Retrieval</h2>
              <p>Kiểm tra các chunk mà hệ thống truy xuất từ kho kiến thức.</p>
            </div>
          </div>
          <button aria-label="Đóng" className={styles.iconButton} type="button" onClick={onClose}><X size={19} /></button>
        </header>

        <form className={styles.retrievalForm} onSubmit={testRetrieval}>
          <label className={styles.retrievalQuery}>
            <Search size={18} />
            <input
              autoFocus
              className="ta-input"
              placeholder="Nhập câu hỏi để kiểm tra retrieval..."
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <label className={styles.topKField}>
            <span>Số kết quả</span>
            <select className="ta-input" value={topK} onChange={(event) => setTopK(Number(event.target.value))}>
              {[3, 5, 10, 20].map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
          </label>
          <button className="ta-btn ta-btn-primary" disabled={!query.trim() || loading} type="submit">
            {loading ? <Loader2 className="animate-spin" size={17} /> : <Search size={17} />}
            {loading ? "Đang truy xuất..." : "Chạy thử"}
          </button>
        </form>

        {error && <div className={styles.error}>{error}</div>}

        <div className={styles.retrievalResults}>
          {!searched && !loading && (
            <div className={styles.retrievalEmpty}>
              <TestTube2 size={31} />
              <strong>Chưa có kết quả kiểm tra</strong>
              <span>Nhập một câu hỏi để xem các chunk được ưu tiên truy xuất.</span>
            </div>
          )}
          {loading && <div className={styles.retrievalEmpty}><Loader2 className="animate-spin" size={28} /><span>Đang tìm các chunk phù hợp...</span></div>}
          {searched && !loading && !error && results.length === 0 && (
            <div className={styles.retrievalEmpty}><Search size={30} /><strong>Không tìm thấy chunk phù hợp.</strong></div>
          )}
          {!loading && results.map((result, index) => (
            <article className={styles.retrievalResult} key={result.id}>
              <div className={styles.retrievalResultTop}>
                <span className={styles.resultRank}>#{index + 1}</span>
                <span className="ta-badge ta-badge-info">Score {Math.round(result.score * 100)}%</span>
                <code>{result.citation_tag}</code>
              </div>
              <h3>{result.section_title || result.document_title}</h3>
              <p>{result.content}</p>
              <footer>
                <span><FileText size={14} /> {result.document_name}</span>
                <button type="button" onClick={() => openDocument(result)}>Mở tài liệu <ArrowRight size={14} /></button>
              </footer>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
