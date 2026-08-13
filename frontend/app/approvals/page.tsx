"use client";

import axios from "axios";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { CheckCircle2, Download, Edit3, Eye, Loader2, RefreshCw, Scale, ShieldAlert, X, XCircle } from "lucide-react";
import Sidebar from "@/components/Sidebar";
import api from "@/lib/api";
import { useAuthStore } from "@/store/useAuthStore";

interface ApprovalItem {
  id: string;
  workflow_id: string;
  workflow_title: string;
  action_type: string;
  risk_level: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
  payload: Record<string, unknown>;
  reason?: string;
  requester?: string;
  data_sources: string[];
  expires_at?: string;
}

interface LegalPreview { filename: string; document_type_label: string; status: string; requester_name: string; content: string; }

function errorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    return String(error.response?.data?.detail || error.message);
  }
  return "Đã xảy ra lỗi không xác định.";
}

export default function ApprovalsCenterPage() {
  const router = useRouter();
  const { isAuthenticated, hasHydrated } = useAuthStore();
  const [approvals, setApprovals] = useState<ApprovalItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editedPayload, setEditedPayload] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [legalPreview, setLegalPreview] = useState<LegalPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  const selected = useMemo(
    () => approvals.find((item) => item.id === selectedId) || null,
    [approvals, selectedId],
  );

  const fetchApprovals = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const { data } = await api.get<ApprovalItem[]>("/api/v1/approvals/pending");
      setApprovals(data);
      setSelectedId((current) =>
        current && data.some((item) => item.id === current) ? current : data[0]?.id || null,
      );
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!hasHydrated) return;
    if (!isAuthenticated) {
      router.replace("/login");
      return;
    }
    const timer = window.setTimeout(() => void fetchApprovals(), 0);
    return () => window.clearTimeout(timer);
  }, [fetchApprovals, hasHydrated, isAuthenticated, router]);

  const act = async (
    action: "APPROVE" | "REJECT" | "EDIT_AND_APPROVE",
  ) => {
    if (!selected) return;
    setSubmitting(true);
    setError(null);
    try {
      let parsedPayload: Record<string, unknown> | undefined;
      if (action === "EDIT_AND_APPROVE") {
        parsedPayload = JSON.parse(editedPayload) as Record<string, unknown>;
      }
      await api.post(`/api/v1/approvals/${selected.id}/action`, {
        action,
        edited_payload: parsedPayload,
      });
      await fetchApprovals();
    } catch (reason) {
      setError(
        reason instanceof SyntaxError
          ? "Payload chỉnh sửa phải là JSON hợp lệ."
          : errorMessage(reason),
      );
    } finally {
      setSubmitting(false);
    }
  };

  const previewLegalDraft = async () => {
    const url = selected?.payload.preview_url;
    if (typeof url !== "string") return;
    setPreviewLoading(true);
    setError(null);
    try {
      const { data } = await api.get<LegalPreview>(url);
      setLegalPreview(data);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setPreviewLoading(false);
    }
  };

  const downloadLegalDraft = async () => {
    const url = selected?.payload.review_download_url;
    if (typeof url !== "string") return;
    setError(null);
    try {
      const response = await api.get<Blob>(url, { responseType: "blob" });
      const objectUrl = URL.createObjectURL(response.data);
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = typeof selected?.payload.filename === "string" ? selected.payload.filename : "legal-draft";
      link.click();
      URL.revokeObjectURL(objectUrl);
    } catch (reason) {
      setError(errorMessage(reason));
    }
  };

  if (!hasHydrated || !isAuthenticated) return null;

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "var(--body-bg)" }}>
      <Sidebar />
      <div style={{ flex: 1, minWidth: 0 }}>
        <header className="ta-topbar">
          <div className="breadcrumb">
            <span>Home</span><span className="breadcrumb-sep">›</span>
            <span className="breadcrumb-current">Human Approval</span>
          </div>
          <button className="ta-btn ta-btn-ghost" onClick={() => void fetchApprovals()}>
            <RefreshCw size={15} /> Làm mới
          </button>
        </header>

        <main style={{ padding: "24px 32px" }}>
          <h1 style={{ display: "flex", alignItems: "center", gap: 10, fontSize: "1.5rem", fontWeight: 800 }}>
            <Scale size={24} color="var(--primary)" /> Trung tâm phê duyệt an toàn
          </h1>
          <p style={{ color: "var(--text-muted)", margin: "6px 0 22px" }}>
            Kiểm tra hành động, dữ liệu đầu vào, lý do, nguồn và mức rủi ro trước khi AI thực thi.
          </p>
          {error && <div className="ta-card" style={{ padding: 14, color: "#B91C1C", marginBottom: 16 }}>{error}</div>}

          <div style={{ display: "grid", gridTemplateColumns: "minmax(280px, .8fr) minmax(420px, 1.4fr)", gap: 20 }}>
            <section>
              <h2 style={{ fontWeight: 700, marginBottom: 10 }}>Chờ duyệt ({approvals.length})</h2>
              {loading ? (
                <div className="ta-card" style={{ padding: 20 }}>Đang tải...</div>
              ) : approvals.length === 0 ? (
                <div className="ta-card" style={{ padding: 24, textAlign: "center" }}>
                  <CheckCircle2 size={30} color="#10B981" style={{ margin: "0 auto 8px" }} />
                  Không có yêu cầu đang chờ.
                </div>
              ) : approvals.map((item) => (
                <button
                  key={item.id}
                  className="ta-card"
                  onClick={() => {
                    setSelectedId(item.id);
                    setEditedPayload(JSON.stringify(item.payload, null, 2));
                  }}
                  style={{
                    width: "100%", textAlign: "left", padding: 15, marginBottom: 10,
                    borderLeft: selectedId === item.id ? "4px solid var(--primary)" : undefined,
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                    <strong>{item.action_type}</strong>
                    <span className={`ta-badge ${item.risk_level === "HIGH" || item.risk_level === "CRITICAL" ? "ta-badge-danger" : "ta-badge-warning"}`}>
                      {item.risk_level}
                    </span>
                  </div>
                  <small style={{ color: "var(--text-muted)" }}>{item.workflow_title}</small>
                </button>
              ))}
            </section>

            <section className="ta-card" style={{ padding: 22 }}>
              {!selected ? (
                <div style={{ textAlign: "center", color: "var(--text-muted)", padding: 30 }}>
                  <ShieldAlert size={30} style={{ margin: "0 auto 8px" }} />
                  Chọn một yêu cầu để xem chi tiết.
                </div>
              ) : (
                <>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 16 }}>
                    <div><h2 style={{ fontWeight: 750 }}>{selected.action_type}</h2><small>{selected.requester || "Không rõ người yêu cầu"}</small></div>
                    <span className="ta-badge ta-badge-warning">{selected.risk_level}</span>
                  </div>
                  <div style={{ padding: 12, background: "#EEF2FF", borderRadius: 8, marginBottom: 14 }}>
                    <strong>Lý do AI:</strong> {selected.reason || "Chưa cung cấp"}
                  </div>
                  <div style={{ marginBottom: 14 }}>
                    <strong>Nguồn dữ liệu:</strong> {selected.data_sources.length ? selected.data_sources.join(", ") : "Chưa khai báo"}
                  </div>
                  {selected.action_type === "LEGAL_DOCUMENT_APPROVAL" ? <div style={{ padding: 14, border: "1px solid #DBE4EC", borderRadius: 9, background: "#F8FAFC" }}>
                    <strong style={{ display: "block", marginBottom: 5 }}>{String(selected.payload.document_type_label || "Văn bản pháp lý")}</strong>
                    <small style={{ color: "var(--text-muted)" }}>{String(selected.payload.filename || "Bản nháp")}</small>
                    <p style={{ margin: "8px 0 12px", color: "var(--text-muted)", fontSize: 13 }}>Xem nội dung hoặc tải bản nháp trước khi quyết định. Payload lưu trữ nội bộ không được hiển thị.</p>
                    <div style={{ display: "flex", gap: 8 }}><button className="ta-btn ta-btn-ghost" disabled={previewLoading} onClick={() => void previewLegalDraft()}>{previewLoading ? <Loader2 className="animate-spin" size={15} /> : <Eye size={15} />} Xem nội dung</button><button className="ta-btn ta-btn-ghost" onClick={() => void downloadLegalDraft()}><Download size={15} /> Tải bản nháp</button></div>
                  </div> : <>
                    <label style={{ fontWeight: 650, display: "block", marginBottom: 6 }}>Payload đề xuất</label>
                    <textarea
                      className="ta-input"
                      rows={14}
                      value={editedPayload || JSON.stringify(selected.payload, null, 2)}
                      onChange={(event) => setEditedPayload(event.target.value)}
                      onFocus={() => {
                        if (!editedPayload) setEditedPayload(JSON.stringify(selected.payload, null, 2));
                      }}
                      style={{ fontFamily: "monospace", fontSize: 12 }}
                    />
                  </>}
                  <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 16 }}>
                    <button disabled={submitting} className="ta-btn" onClick={() => void act("REJECT")}><XCircle size={15} /> Từ chối</button>
                    {selected.action_type !== "LEGAL_DOCUMENT_APPROVAL" && <button disabled={submitting} className="ta-btn ta-btn-ghost" onClick={() => void act("EDIT_AND_APPROVE")}><Edit3 size={15} /> Sửa & duyệt</button>}
                    <button disabled={submitting} className="ta-btn ta-btn-primary" onClick={() => void act("APPROVE")}><CheckCircle2 size={15} /> Phê duyệt</button>
                  </div>
                </>
              )}
            </section>
          </div>
        </main>
      </div>
      {legalPreview && <div role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setLegalPreview(null); }} style={{ position: "fixed", zIndex: 500, inset: 0, display: "grid", placeItems: "center", padding: 24, background: "rgba(15,23,42,.52)" }}><section role="dialog" aria-modal="true" style={{ width: "min(900px, 100%)", maxHeight: "calc(100vh - 48px)", display: "flex", flexDirection: "column", overflow: "hidden", borderRadius: 12, background: "#fff", boxShadow: "0 24px 70px rgba(15,23,42,.3)" }}><header style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "14px 17px", borderBottom: "1px solid #E2E8F0" }}><div><strong>{legalPreview.document_type_label}</strong><small style={{ display: "block", marginTop: 3, color: "#64748B" }}>{legalPreview.filename} · {legalPreview.requester_name}</small></div><button className="ta-btn ta-btn-ghost" onClick={() => setLegalPreview(null)}><X size={16} /></button></header><pre style={{ minHeight: 400, margin: 0, padding: 20, overflow: "auto", color: "#334155", font: "400 13px/1.7 Inter, sans-serif", whiteSpace: "pre-wrap" }}>{legalPreview.content}</pre></section></div>}
    </div>
  );
}
