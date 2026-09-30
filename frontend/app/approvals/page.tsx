"use client";

import axios from "axios";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, CheckCircle2, Download, Edit3, Eye, Loader2, RefreshCw, Scale, Send, ShieldAlert, X, XCircle } from "lucide-react";
import Sidebar from "@/components/Sidebar";
import ContractFileViewer, { ContractFile } from "@/components/legal/ContractFileViewer";
import api from "@/lib/api";
import { useAuthStore, userCan } from "@/store/useAuthStore";

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
  status?: "WAITING" | "APPROVED" | "REJECTED" | "EXPIRED";
  comments?: string | null;
  // Only on the requester's own list (/approvals/submitted).
  decided_at?: string | null;
  approver_name?: string | null;
  eligible_approver_count?: number;
  warning?: string | null;
}

type Tab = "pending" | "submitted";

const STATUS_LABELS: Record<NonNullable<ApprovalItem["status"]>, string> = {
  WAITING: "Đang chờ duyệt",
  APPROVED: "Đã phê duyệt",
  REJECTED: "Bị từ chối",
  EXPIRED: "Hết hạn",
};

// What a contract-review approval carries (legal_approval_service + submit-approval).
interface ContractApprovalFile { filename: string; format: string; url: string; revised_at?: string; applied?: number; skipped?: number }
interface ContractApprovalPayload {
  document_name?: string;
  risk_score?: number;
  contract_review_id?: string;
  review_url?: string;
  note?: string | null;
  submitted_manually?: boolean;
  decision_summary?: Record<"ACCEPTED" | "EDITED" | "REJECTED" | "PENDING", number>;
  revised_document?: ContractApprovalFile | null;
  original_document?: ContractApprovalFile | null;
  findings?: Array<{ finding_key: string; clause?: string; clause_title?: string; severity?: string; issue?: string }>;
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
  const { isAuthenticated, hasHydrated, user } = useAuthStore();
  // "Xem mọi bản rà soát hợp đồng" in org-structure: who may open a review they did not run.
  const canOpenContracts = userCan(user, "legal.review.view_all");
  const [approvals, setApprovals] = useState<ApprovalItem[]>([]);
  // What the viewer asked for: /pending never lists it, since no one signs their own request.
  const [submittedItems, setSubmittedItems] = useState<ApprovalItem[]>([]);
  const [tab, setTab] = useState<Tab>("pending");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editedPayload, setEditedPayload] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [legalPreview, setLegalPreview] = useState<LegalPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [contractViewer, setContractViewer] = useState<{ title: string; files: ContractFile[]; initialKey: string } | null>(null);

  const tabRef = useRef<Tab>("pending");
  const list = tab === "pending" ? approvals : submittedItems;
  const readOnly = tab === "submitted";
  const selected = useMemo(
    () => list.find((item) => item.id === selectedId) || null,
    [list, selectedId],
  );

  const fetchApprovals = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [pending, mine] = await Promise.all([
        api.get<ApprovalItem[]>("/api/v1/approvals/pending"),
        api.get<ApprovalItem[]>("/api/v1/approvals/submitted"),
      ]);
      setApprovals(pending.data);
      setSubmittedItems(mine.data);
      const shown = tabRef.current === "pending" ? pending.data : mine.data;
      setSelectedId((current) =>
        current && shown.some((item) => item.id === current) ? current : shown[0]?.id || null,
      );
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setLoading(false);
    }
  }, []);

  const switchTab = (next: Tab) => {
    tabRef.current = next;
    setTab(next);
    setSelectedId((next === "pending" ? approvals : submittedItems)[0]?.id || null);
    setEditedPayload("");
  };

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

  const contractFiles = (payload: ContractApprovalPayload): ContractFile[] => {
    const files: ContractFile[] = [];
    if (payload.revised_document) files.push({ key: "revised", label: "Bản đã sửa", ...payload.revised_document });
    if (payload.original_document) files.push({ key: "original", label: "Bản gốc", ...payload.original_document });
    return files;
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
              <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
                <button className={`ta-btn ${tab === "pending" ? "ta-btn-primary" : "ta-btn-ghost"}`} onClick={() => switchTab("pending")}>
                  Chờ tôi duyệt ({approvals.length})
                </button>
                <button className={`ta-btn ${tab === "submitted" ? "ta-btn-primary" : "ta-btn-ghost"}`} onClick={() => switchTab("submitted")}>
                  <Send size={14} /> Tôi đã gửi ({submittedItems.length})
                </button>
              </div>
              {loading ? (
                <div className="ta-card" style={{ padding: 20 }}>Đang tải...</div>
              ) : list.length === 0 ? (
                <div className="ta-card" style={{ padding: 24, textAlign: "center" }}>
                  <CheckCircle2 size={30} color="#10B981" style={{ margin: "0 auto 8px" }} />
                  {readOnly ? "Bạn chưa gửi yêu cầu phê duyệt nào." : "Không có yêu cầu đang chờ."}
                </div>
              ) : list.map((item) => (
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
                  {readOnly && item.status && (
                    <small style={{ display: "flex", alignItems: "center", gap: 4, marginTop: 6, color: item.warning ? "#B91C1C" : "var(--text-muted)" }}>
                      {item.warning && <AlertTriangle size={13} />}
                      {STATUS_LABELS[item.status]}{item.warning ? " · chưa ai duyệt được" : ""}
                    </small>
                  )}
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
                  {readOnly && selected.status && (
                    <div style={{ padding: 12, borderRadius: 8, marginBottom: 14, background: selected.warning ? "#FEF2F2" : "#F1F5F9", color: selected.warning ? "#991B1B" : "#334155" }}>
                      <strong>{STATUS_LABELS[selected.status]}</strong>
                      {selected.status === "WAITING" && !selected.warning && selected.eligible_approver_count
                        ? ` · ${selected.eligible_approver_count} người có quyền duyệt đang thấy yêu cầu này.`
                        : null}
                      {selected.status !== "WAITING" && (selected.approver_name || selected.decided_at)
                        ? ` · ${selected.approver_name || ""}${selected.decided_at ? ` lúc ${new Date(selected.decided_at).toLocaleString("vi-VN")}` : ""}`
                        : null}
                      {selected.warning && <p style={{ margin: "6px 0 0", display: "flex", gap: 6 }}><AlertTriangle size={15} style={{ flexShrink: 0, marginTop: 2 }} />{selected.warning}</p>}
                      {selected.status === "REJECTED" && selected.comments && <p style={{ margin: "6px 0 0" }}>Lý do: {selected.comments}</p>}
                    </div>
                  )}
                  <div style={{ padding: 12, background: "#EEF2FF", borderRadius: 8, marginBottom: 14 }}>
                    <strong>Lý do AI:</strong> {selected.reason || "Chưa cung cấp"}
                  </div>
                  <div style={{ marginBottom: 14 }}>
                    <strong>Nguồn dữ liệu:</strong> {selected.data_sources.length ? selected.data_sources.join(", ") : "Chưa khai báo"}
                  </div>
                  {selected.action_type === "LEGAL_CONTRACT_APPROVAL" && selected.payload.contract_review_id ? (() => {
                    const payload = selected.payload as ContractApprovalPayload;
                    const files = contractFiles(payload);
                    const summary = payload.decision_summary;
                    return <div style={{ padding: 14, border: "1px solid #DBE4EC", borderRadius: 9, background: "#F8FAFC" }}>
                      <strong style={{ display: "block", marginBottom: 4 }}>{payload.document_name || "Hợp đồng"}</strong>
                      <small style={{ color: "var(--text-muted)" }}>
                        Điểm rủi ro {payload.risk_score ?? "—"}/100 · {payload.submitted_manually ? "Người rà soát tự gửi duyệt" : "Tự động tạo do rủi ro cao"}
                      </small>
                      {summary && <p style={{ margin: "8px 0 0", fontSize: 13 }}>
                        Quyết định: <b>{summary.ACCEPTED}</b> chấp nhận · <b>{summary.EDITED}</b> tự chỉnh · <b>{summary.REJECTED}</b> từ chối · <b>{summary.PENDING}</b> chưa xét
                      </p>}
                      {payload.note && <p style={{ margin: "8px 0 0", padding: 8, borderRadius: 6, background: "#fff", fontSize: 13 }}><b>Ghi chú:</b> {payload.note}</p>}
                      <p style={{ margin: "8px 0 12px", color: "var(--text-muted)", fontSize: 13 }}>
                        {payload.revised_document
                          ? `Bản đã sửa: ${payload.revised_document.filename} · ${payload.revised_document.applied ?? 0} điều đã ghi${payload.revised_document.skipped ? `, ${payload.revised_document.skipped} điều cần sửa tay` : ""}.`
                          : "Chưa có bản hợp đồng đã sửa kèm theo; xem kết quả rà soát để quyết định."}
                      </p>
                      {payload.findings && payload.findings.length > 0 && <ul style={{ margin: "0 0 12px", paddingLeft: 18, fontSize: 13, color: "#334155" }}>
                        {payload.findings.slice(0, 5).map((item) => <li key={item.finding_key}>[{item.severity}] {item.clause && item.clause !== "MISSING" ? `Điều ${item.clause}: ` : ""}{item.issue}</li>)}
                        {payload.findings.length > 5 && <li style={{ listStyle: "none", color: "var(--text-muted)" }}>+{payload.findings.length - 5} phát hiện khác</li>}
                      </ul>}
                      {/* The review and its files stay with its author and the executives; other
                          approvers decide from the summary above. On "Tôi đã gửi" the viewer is
                          that author. */}
                      {canOpenContracts || readOnly ? <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                        {files.length > 0 && <button className="ta-btn ta-btn-ghost" onClick={() => setContractViewer({ title: payload.document_name || "Hợp đồng", files, initialKey: files[0].key })}><Eye size={15} /> Xem {files[0].label.toLowerCase()}</button>}
                        {payload.review_url && <button className="ta-btn ta-btn-ghost" onClick={() => router.push(payload.review_url as string)}><Scale size={15} /> Mở bản rà soát</button>}
                      </div> : <small style={{ color: "var(--text-muted)" }}>Nội dung hợp đồng và file đã sửa chỉ chức vụ có quyền “Xem mọi bản rà soát hợp đồng” mở được.</small>}
                    </div>;
                  })() : selected.action_type === "LEGAL_DOCUMENT_APPROVAL" ? <div style={{ padding: 14, border: "1px solid #DBE4EC", borderRadius: 9, background: "#F8FAFC" }}>
                    <strong style={{ display: "block", marginBottom: 5 }}>{String(selected.payload.document_type_label || "Văn bản pháp lý")}</strong>
                    <small style={{ color: "var(--text-muted)" }}>{String(selected.payload.filename || "Bản nháp")}</small>
                    <p style={{ margin: "8px 0 12px", color: "var(--text-muted)", fontSize: 13 }}>Xem nội dung hoặc tải bản nháp trước khi quyết định. Payload lưu trữ nội bộ không được hiển thị.</p>
                    <div style={{ display: "flex", gap: 8 }}><button className="ta-btn ta-btn-ghost" disabled={previewLoading} onClick={() => void previewLegalDraft()}>{previewLoading ? <Loader2 className="animate-spin" size={15} /> : <Eye size={15} />} Xem nội dung</button><button className="ta-btn ta-btn-ghost" onClick={() => void downloadLegalDraft()}><Download size={15} /> Tải bản nháp</button></div>
                  </div> : <>
                    <label style={{ fontWeight: 650, display: "block", marginBottom: 6 }}>Payload đề xuất</label>
                    <textarea
                      className="ta-input"
                      rows={14}
                      readOnly={readOnly}
                      value={editedPayload || JSON.stringify(selected.payload, null, 2)}
                      onChange={(event) => setEditedPayload(event.target.value)}
                      onFocus={() => {
                        if (!editedPayload) setEditedPayload(JSON.stringify(selected.payload, null, 2));
                      }}
                      style={{ fontFamily: "monospace", fontSize: 12 }}
                    />
                  </>}
                  {!readOnly && <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 16 }}>
                    <button disabled={submitting} className="ta-btn" onClick={() => void act("REJECT")}><XCircle size={15} /> Từ chối</button>
                    {!["LEGAL_DOCUMENT_APPROVAL", "LEGAL_CONTRACT_APPROVAL"].includes(selected.action_type) && <button disabled={submitting} className="ta-btn ta-btn-ghost" onClick={() => void act("EDIT_AND_APPROVE")}><Edit3 size={15} /> Sửa & duyệt</button>}
                    <button disabled={submitting} className="ta-btn ta-btn-primary" onClick={() => void act("APPROVE")}><CheckCircle2 size={15} /> Phê duyệt</button>
                  </div>}
                </>
              )}
            </section>
          </div>
        </main>
      </div>
      {contractViewer && <ContractFileViewer title={contractViewer.title} files={contractViewer.files} initialKey={contractViewer.initialKey} onClose={() => setContractViewer(null)} />}
      {legalPreview && <div role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setLegalPreview(null); }} style={{ position: "fixed", zIndex: 500, inset: 0, display: "grid", placeItems: "center", padding: 24, background: "rgba(15,23,42,.52)" }}><section role="dialog" aria-modal="true" style={{ width: "min(900px, 100%)", maxHeight: "calc(100vh - 48px)", display: "flex", flexDirection: "column", overflow: "hidden", borderRadius: 12, background: "#fff", boxShadow: "0 24px 70px rgba(15,23,42,.3)" }}><header style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "14px 17px", borderBottom: "1px solid #E2E8F0" }}><div><strong>{legalPreview.document_type_label}</strong><small style={{ display: "block", marginTop: 3, color: "#64748B" }}>{legalPreview.filename} · {legalPreview.requester_name}</small></div><button className="ta-btn ta-btn-ghost" onClick={() => setLegalPreview(null)}><X size={16} /></button></header><pre style={{ minHeight: 400, margin: 0, padding: 20, overflow: "auto", color: "#334155", font: "400 13px/1.7 Inter, sans-serif", whiteSpace: "pre-wrap" }}>{legalPreview.content}</pre></section></div>}
    </div>
  );
}
