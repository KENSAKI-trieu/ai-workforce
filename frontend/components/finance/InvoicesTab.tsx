"use client";

import { AlertTriangle, CheckCircle2, FileUp, Loader2, Send, XCircle } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import api from "@/lib/api";
import { INVOICE_STATUS, apiError, formatVnd } from "@/components/finance/format";

interface InvoiceException { code: string; message: string; severity: "BLOCKING" | "INFO"; resolved_by?: string; resolution?: string }
interface Invoice {
  id: string;
  direction: "IN" | "OUT";
  party_name?: string | null;
  seller_tax_code: string;
  series: string;
  number: string;
  issue_date?: string | null;
  due_date?: string | null;
  amount_before_tax: string;
  vat_amount: string;
  total_amount: string;
  po_number?: string | null;
  status: string;
  exceptions: InvoiceException[];
  source_format: string;
  source_filename?: string | null;
  lines?: { name: string; quantity?: string; amount: string; vat_rate?: string }[];
}
interface UploadResult { filename: string; created: boolean; message?: string; error?: string; invoice?: Invoice }

export default function InvoicesTab({ canUpload, canPropose }: { canUpload: boolean; canPropose: boolean }) {
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [statusFilter, setStatusFilter] = useState("");
  const [selected, setSelected] = useState<Invoice | null>(null);
  const [direction, setDirection] = useState<"IN" | "OUT">("IN");
  const [uploading, setUploading] = useState(false);
  const [results, setResults] = useState<UploadResult[]>([]);
  const [note, setNote] = useState("");
  const [account, setAccount] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "ok" | "error"; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get<Invoice[]>("/api/v1/finance/invoices", { params: statusFilter ? { status: statusFilter } : {} });
      setInvoices(data);
    } catch (error) {
      setMessage({ tone: "error", text: apiError(error).message });
    }
  }, [statusFilter]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const open = async (id: string) => {
    setMessage(null);
    setNote("");
    setAccount("");
    try {
      const { data } = await api.get<Invoice>(`/api/v1/finance/invoices/${id}`);
      setSelected(data);
    } catch (error) {
      setMessage({ tone: "error", text: apiError(error).message });
    }
  };

  const upload = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setUploading(true);
    setMessage(null);
    const form = new FormData();
    Array.from(files).forEach((file) => form.append("files", file));
    try {
      const { data } = await api.post<UploadResult[]>("/api/v1/finance/invoices/upload", form, { params: { direction } });
      setResults(data);
      await load();
    } catch (error) {
      setMessage({ tone: "error", text: apiError(error).message });
    } finally {
      setUploading(false);
    }
  };

  const review = async (action: "ACCEPT" | "REJECT") => {
    if (!selected) return;
    setBusy(true);
    try {
      const { data } = await api.post<Invoice>(`/api/v1/finance/invoices/${selected.id}/review`, { action, note });
      setSelected(data);
      setNote("");
      await load();
    } catch (error) {
      setMessage({ tone: "error", text: apiError(error).message });
    } finally {
      setBusy(false);
    }
  };

  const propose = async () => {
    if (!selected) return;
    setBusy(true);
    setMessage(null);
    try {
      const { data } = await api.post(`/api/v1/finance/invoices/${selected.id}/propose-entry`, account ? { main_account: account } : {});
      setMessage({
        tone: "ok",
        text: data.created
          ? "Đã lập bút toán nháp và gửi tới Trung tâm phê duyệt."
          : "Hoá đơn này đã có bút toán chờ duyệt hoặc đã ghi sổ.",
      });
      await open(selected.id);
    } catch (error) {
      setMessage({ tone: "error", text: apiError(error).message });
    } finally {
      setBusy(false);
    }
  };

  const blocking = (selected?.exceptions || []).filter((item) => item.severity === "BLOCKING");

  return (
    <div>
      {canUpload && (
        <section className="ta-card" style={{ padding: 16, marginBottom: 16 }}>
          <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 10 }}>
            <strong>Tải hoá đơn điện tử</strong>
            <select className="ta-input" style={{ width: 160 }} value={direction} onChange={(event) => setDirection(event.target.value as "IN" | "OUT")}>
              <option value="IN">Hoá đơn mua vào</option>
              <option value="OUT">Hoá đơn bán ra</option>
            </select>
            <label className="ta-btn ta-btn-primary" style={{ cursor: "pointer" }}>
              {uploading ? <Loader2 className="animate-spin" size={15} /> : <FileUp size={15} />} Chọn file XML/PDF
              <input type="file" multiple accept=".xml,.pdf" hidden onChange={(event) => { void upload(event.target.files); event.target.value = ""; }} />
            </label>
            <small style={{ color: "var(--text-muted)" }}>Ưu tiên file XML (hoá đơn điện tử gốc). PDF scan chưa được hỗ trợ.</small>
          </div>
          {results.length > 0 && (
            <ul style={{ margin: "12px 0 0", paddingLeft: 18, fontSize: 13 }}>
              {results.map((result, index) => (
                <li key={`${result.filename}-${index}`} style={{ color: result.error ? "#B91C1C" : undefined }}>
                  <b>{result.filename}</b>: {result.error || result.message}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
      {message && <div className="ta-card" style={{ padding: 12, marginBottom: 12, color: message.tone === "error" ? "#B91C1C" : "#047857" }}>{message.text}</div>}

      <div style={{ display: "grid", gridTemplateColumns: "minmax(320px, 1fr) minmax(380px, 1.1fr)", gap: 16 }}>
        <section>
          <div style={{ display: "flex", gap: 6, marginBottom: 10, flexWrap: "wrap" }}>
            {["", "EXCEPTION", "MATCHED", "POSTED", "PAID"].map((status) => (
              <button key={status || "all"} className={`ta-btn ${statusFilter === status ? "ta-btn-primary" : "ta-btn-ghost"}`} onClick={() => setStatusFilter(status)}>
                {status ? INVOICE_STATUS[status].label : "Tất cả"}
              </button>
            ))}
          </div>
          {invoices.length === 0 ? (
            <div className="ta-card" style={{ padding: 20, textAlign: "center", color: "var(--text-muted)" }}>Chưa có hoá đơn.</div>
          ) : invoices.map((invoice) => (
            <button
              key={invoice.id}
              className="ta-card"
              onClick={() => void open(invoice.id)}
              style={{ width: "100%", textAlign: "left", padding: 12, marginBottom: 8, borderLeft: selected?.id === invoice.id ? "4px solid var(--primary)" : undefined }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                <strong>{invoice.series} số {invoice.number}</strong>
                <span className={`ta-badge ${INVOICE_STATUS[invoice.status]?.badge || "ta-badge-neutral"}`}>{INVOICE_STATUS[invoice.status]?.label || invoice.status}</span>
              </div>
              <small style={{ color: "var(--text-muted)" }}>
                {invoice.direction === "IN" ? "Mua vào" : "Bán ra"} · {invoice.party_name} · {invoice.issue_date || "—"} · {formatVnd(invoice.total_amount)}
              </small>
            </button>
          ))}
        </section>

        <section className="ta-card" style={{ padding: 18 }}>
          {!selected ? (
            <div style={{ textAlign: "center", color: "var(--text-muted)", padding: 30 }}>Chọn một hoá đơn để xem chi tiết.</div>
          ) : (
            <>
              <h3 style={{ fontWeight: 750, marginBottom: 4 }}>Hoá đơn {selected.series} số {selected.number}</h3>
              <small style={{ color: "var(--text-muted)" }}>
                {selected.party_name} · MST {selected.seller_tax_code || "—"} · {selected.source_format}{selected.po_number ? ` · PO ${selected.po_number}` : ""}
              </small>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8, margin: "12px 0" }}>
                <Figure label="Trước thuế" value={formatVnd(selected.amount_before_tax)} />
                <Figure label="Thuế GTGT" value={formatVnd(selected.vat_amount)} />
                <Figure label="Tổng thanh toán" value={formatVnd(selected.total_amount)} />
              </div>
              {selected.exceptions.length > 0 && (
                <ul style={{ margin: "0 0 12px", padding: 0, listStyle: "none", display: "grid", gap: 6 }}>
                  {selected.exceptions.map((item, index) => (
                    <li key={`${item.code}-${index}`} style={{
                      padding: "8px 10px", borderRadius: 6, fontSize: 13, display: "flex", gap: 6,
                      background: item.severity === "BLOCKING" && !item.resolved_by ? "#FEF2F2" : "#F1F5F9",
                      color: item.severity === "BLOCKING" && !item.resolved_by ? "#991B1B" : "#334155",
                    }}>
                      <AlertTriangle size={14} style={{ flexShrink: 0, marginTop: 2 }} />
                      <span>
                        {item.message}
                        {item.resolved_by && <em style={{ display: "block", color: "var(--text-muted)" }}>Đã xử lý bởi {item.resolved_by}: {item.resolution}</em>}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
              {(selected.lines || []).length > 0 && (
                <table className="ta-table" style={{ width: "100%", fontSize: 13, marginBottom: 12 }}>
                  <thead><tr><th>Hàng hoá, dịch vụ</th><th>SL</th><th>Thuế</th><th style={{ textAlign: "right" }}>Thành tiền</th></tr></thead>
                  <tbody>
                    {(selected.lines || []).map((line, index) => (
                      <tr key={index}><td>{line.name}</td><td>{line.quantity}</td><td>{line.vat_rate}</td><td style={{ textAlign: "right" }}>{formatVnd(line.amount)}</td></tr>
                    ))}
                  </tbody>
                </table>
              )}
              {canUpload && selected.status === "EXCEPTION" && (
                <div style={{ display: "grid", gap: 8, marginBottom: 12 }}>
                  <textarea className="ta-input" rows={2} placeholder="Ghi chú xử lý (bắt buộc), ví dụ: đã gọi xác nhận với NCC" value={note} onChange={(event) => setNote(event.target.value)} />
                  <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
                    <button className="ta-btn" disabled={busy || note.trim().length < 3} onClick={() => void review("REJECT")}><XCircle size={15} /> Loại hoá đơn</button>
                    <button className="ta-btn ta-btn-primary" disabled={busy || note.trim().length < 3} onClick={() => void review("ACCEPT")}>
                      <CheckCircle2 size={15} /> Đã kiểm tra {blocking.length} ngoại lệ
                    </button>
                  </div>
                </div>
              )}
              {canPropose && selected.status === "MATCHED" && (
                <div style={{ display: "flex", gap: 8, alignItems: "center", justifyContent: "flex-end" }}>
                  <input className="ta-input" style={{ width: 150 }} placeholder="TK (tuỳ chọn)" value={account} onChange={(event) => setAccount(event.target.value.replace(/\D/g, ""))} />
                  <button className="ta-btn ta-btn-primary" disabled={busy} onClick={() => void propose()}>
                    {busy ? <Loader2 className="animate-spin" size={15} /> : <Send size={15} />} Đề xuất bút toán
                  </button>
                </div>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
}

function Figure({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ padding: 10, borderRadius: 8, background: "#F8FAFC", border: "1px solid #E2E8F0" }}>
      <small style={{ color: "var(--text-muted)" }}>{label}</small>
      <div style={{ fontWeight: 700 }}>{value}</div>
    </div>
  );
}
