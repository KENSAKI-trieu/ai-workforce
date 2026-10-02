"use client";

import { Download, FileSpreadsheet, Loader2, RotateCcw, Save } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import api from "@/lib/api";
import { ImportError, apiError, downloadBlob, formatVnd } from "@/components/finance/format";

interface Settings {
  chart: string;
  company_tax_code?: string | null;
  approval_thresholds: { up_to: string; permission: string }[];
  po_tolerance_percent: string;
  account_count: number;
}
interface ImportKind { kind: string; title: string; guide: string; undoable: boolean; columns: { key: string; label: string; required: boolean }[] }
interface Batch { id: string; kind: string; filename: string; row_count: number; status: string; undoable: boolean; created_at?: string | null }

const LEVEL_LABEL: Record<string, string> = {
  "finance.journal.approve": "Duyệt mức thường",
  "finance.journal.approve_high": "Duyệt mức cao",
};

export default function DataTab() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [form, setForm] = useState({ chart: "TT200", company_tax_code: "", low: "", high: "", tolerance: "0" });
  const [kinds, setKinds] = useState<ImportKind[]>([]);
  const [batches, setBatches] = useState<Batch[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ tone: "ok" | "error"; text: string; rows?: ImportError[] } | null>(null);

  const load = useCallback(async () => {
    try {
      const [settingsResponse, kindsResponse, batchesResponse] = await Promise.all([
        api.get<Settings>("/api/v1/finance/settings"),
        api.get<ImportKind[]>("/api/v1/finance/import/kinds"),
        api.get<Batch[]>("/api/v1/finance/import/batches"),
      ]);
      const current = settingsResponse.data;
      setSettings(current);
      setForm({
        chart: current.chart,
        company_tax_code: current.company_tax_code || "",
        low: String(Math.round(Number(current.approval_thresholds[0]?.up_to || 0))),
        high: String(Math.round(Number(current.approval_thresholds[1]?.up_to || 0))),
        tolerance: String(Number(current.po_tolerance_percent)),
      });
      setKinds(kindsResponse.data);
      setBatches(batchesResponse.data);
    } catch (error) {
      setMessage({ tone: "error", text: apiError(error).message });
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const save = async () => {
    setBusy("settings");
    setMessage(null);
    try {
      const { data } = await api.put("/api/v1/finance/settings", {
        chart: form.chart,
        company_tax_code: form.company_tax_code || undefined,
        po_tolerance_percent: form.tolerance || "0",
        approval_thresholds: [
          { up_to: Number(form.low), permission: "finance.journal.approve" },
          { up_to: Number(form.high), permission: "finance.journal.approve_high" },
        ],
      });
      setMessage({ tone: "ok", text: `Đã lưu cài đặt${data.accounts_added ? `, thêm ${data.accounts_added} tài khoản` : ""}.` });
      await load();
    } catch (error) {
      setMessage({ tone: "error", text: apiError(error).message });
    } finally {
      setBusy(null);
    }
  };

  const upload = async (kind: string, files: FileList | null) => {
    const file = files?.[0];
    if (!file) return;
    setBusy(kind);
    setMessage(null);
    const body = new FormData();
    body.append("file", file);
    try {
      const { data } = await api.post<Batch>(`/api/v1/finance/import/${kind}`, body);
      setMessage({ tone: "ok", text: `Đã nhập ${data.row_count} dòng từ ${data.filename}.` });
      await load();
    } catch (error) {
      const problem = apiError(error);
      setMessage({ tone: "error", text: problem.message, rows: problem.rows });
    } finally {
      setBusy(null);
    }
  };

  const undo = async (batch: Batch) => {
    if (!window.confirm(`Hoàn tác lần nhập ${batch.filename}? Mọi dòng của lần nhập này sẽ bị xoá.`)) return;
    setBusy(batch.id);
    try {
      const { data } = await api.delete(`/api/v1/finance/import/batches/${batch.id}`);
      setMessage({ tone: "ok", text: `Đã hoàn tác, xoá ${data.rows_removed} dòng.` });
      await load();
    } catch (error) {
      setMessage({ tone: "error", text: apiError(error).message });
    } finally {
      setBusy(null);
    }
  };

  const template = (kind: string) => downloadBlob(`/api/v1/finance/import/${kind}/template`, `mau-nhap-${kind}.xlsx`,
    (url) => api.get<Blob>(url, { responseType: "blob" })).catch((error) => setMessage({ tone: "error", text: apiError(error).message }));

  return (
    <div style={{ display: "grid", gap: 16 }}>
      {message && (
        <div className="ta-card" style={{ padding: 12, color: message.tone === "error" ? "#B91C1C" : "#047857" }}>
          {message.text}
          {message.rows && message.rows.length > 0 && (
            <ul style={{ margin: "8px 0 0", paddingLeft: 18, fontSize: 13, maxHeight: 220, overflowY: "auto" }}>
              {message.rows.map((row, index) => (
                <li key={index}>{row.row ? `Dòng ${row.row}` : "File"}{row.column ? ` · ${row.column}` : ""}: {row.message}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {settings && (
        <section className="ta-card" style={{ padding: 16 }}>
          <strong style={{ display: "block", marginBottom: 12 }}>Cài đặt tài chính</strong>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 12 }}>
            <label style={field}>Chế độ kế toán
              <select className="ta-input" value={form.chart} onChange={(event) => setForm({ ...form, chart: event.target.value })}>
                <option value="TT200">Thông tư 200</option>
                <option value="TT133">Thông tư 133 (DN nhỏ và vừa)</option>
              </select>
            </label>
            <label style={field}>MST công ty
              <input className="ta-input" value={form.company_tax_code} onChange={(event) => setForm({ ...form, company_tax_code: event.target.value })} placeholder="0101234567" />
            </label>
            <label style={field}>{LEVEL_LABEL["finance.journal.approve"]} đến (đ)
              <input className="ta-input" value={form.low} onChange={(event) => setForm({ ...form, low: event.target.value.replace(/\D/g, "") })} />
            </label>
            <label style={field}>{LEVEL_LABEL["finance.journal.approve_high"]} đến (đ)
              <input className="ta-input" value={form.high} onChange={(event) => setForm({ ...form, high: event.target.value.replace(/\D/g, "") })} />
            </label>
            <label style={field}>Sai lệch cho phép với PO (%)
              <input className="ta-input" value={form.tolerance} onChange={(event) => setForm({ ...form, tolerance: event.target.value })} />
            </label>
          </div>
          <p style={{ margin: "10px 0", fontSize: 13, color: "var(--text-muted)" }}>
            Đến {formatVnd(form.low)}: người có quyền “{LEVEL_LABEL["finance.journal.approve"]}”. Đến {formatVnd(form.high)}: “{LEVEL_LABEL["finance.journal.approve_high"]}”.
            Trên mức đó: “Phê duyệt yêu cầu tối quan trọng”. Hệ thống đang có {settings.account_count} tài khoản; đổi chế độ chỉ thêm tài khoản còn thiếu.
          </p>
          <button className="ta-btn ta-btn-primary" disabled={busy === "settings"} onClick={() => void save()}>
            {busy === "settings" ? <Loader2 className="animate-spin" size={15} /> : <Save size={15} />} Lưu cài đặt
          </button>
        </section>
      )}

      <section className="ta-card" style={{ padding: 16 }}>
        <strong style={{ display: "block", marginBottom: 6 }}>Nhập dữ liệu từ Excel</strong>
        <p style={{ margin: "0 0 12px", fontSize: 13, color: "var(--text-muted)" }}>
          Xuất từ phần mềm kế toán (MISA, Fast…), đổi tên dòng tiêu đề theo mẫu. Cả file được kiểm tra trước khi ghi: có một dòng lỗi thì không dòng nào được nhập.
        </p>
        <div style={{ display: "grid", gap: 8 }}>
          {kinds.map((kind) => (
            <div key={kind.kind} style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 10, padding: 10, border: "1px solid #E2E8F0", borderRadius: 8 }}>
              <div style={{ flex: "1 1 260px" }}>
                <b>{kind.title}</b>
                <small style={{ display: "block", color: "var(--text-muted)" }}>{kind.guide}</small>
              </div>
              <button className="ta-btn ta-btn-ghost" onClick={() => void template(kind.kind)}><Download size={14} /> Mẫu</button>
              <label className="ta-btn ta-btn-outline" style={{ cursor: "pointer" }}>
                {busy === kind.kind ? <Loader2 className="animate-spin" size={14} /> : <FileSpreadsheet size={14} />} Nhập file
                <input type="file" accept=".xlsx" hidden onChange={(event) => { void upload(kind.kind, event.target.files); event.target.value = ""; }} />
              </label>
            </div>
          ))}
        </div>
      </section>

      {batches.length > 0 && (
        <section className="ta-card" style={{ padding: 16 }}>
          <strong style={{ display: "block", marginBottom: 10 }}>Các lần nhập</strong>
          <table className="ta-table" style={{ width: "100%", fontSize: 13 }}>
            <thead><tr><th>Thời điểm</th><th>Loại</th><th>File</th><th>Số dòng</th><th>Trạng thái</th><th /></tr></thead>
            <tbody>
              {batches.map((batch) => (
                <tr key={batch.id}>
                  <td>{batch.created_at ? new Date(batch.created_at).toLocaleString("vi-VN") : ""}</td>
                  <td>{kinds.find((kind) => kind.kind === batch.kind)?.title || batch.kind}</td>
                  <td>{batch.filename}</td><td>{batch.row_count}</td>
                  <td>{batch.status === "ROLLED_BACK" ? "Đã hoàn tác" : "Đã nhập"}</td>
                  <td>{batch.undoable && batch.status !== "ROLLED_BACK" && (
                    <button className="ta-btn ta-btn-ghost" disabled={busy === batch.id} onClick={() => void undo(batch)}><RotateCcw size={14} /> Hoàn tác</button>
                  )}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}

const field: React.CSSProperties = { display: "grid", gap: 4, fontSize: 13, fontWeight: 600 };
