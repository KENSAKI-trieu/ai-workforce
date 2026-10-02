"use client";

import { Banknote, Loader2, Mail } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import api from "@/lib/api";
import { apiError, formatVnd } from "@/components/finance/format";

interface AgingParty { party_id: string | null; party: string; tax_code?: string | null; NOT_DUE: string; "1_30": string; "31_60": string; "61_90": string; OVER_90: string; total: string }
interface Aging { kind: string; as_of: string; buckets: Record<string, string>; total: string; parties: AgingParty[] }
interface ScheduleRow { invoice_id: string; party: string; series: string; number: string; due_date: string; overdue: boolean; remaining: string; already_scheduled: string; to_schedule: string }
interface Schedule { invoices: ScheduleRow[]; total_to_schedule: string; until: string }
interface Voucher { workflow_id: string; reference: string; status: string; total: string; invoice_count: number; created_at?: string | null }

const BUCKETS: [keyof AgingParty, string][] = [["NOT_DUE", "Chưa đến hạn"], ["1_30", "1–30 ngày"], ["31_60", "31–60"], ["61_90", "61–90"], ["OVER_90", "Trên 90"]];
const VOUCHER_STATUS: Record<string, string> = { DRAFT: "Chờ duyệt", SCHEDULED: "Đã duyệt, chờ chuyển tiền", PAID: "Đã thanh toán", CANCELLED: "Bị từ chối" };

export default function DebtsTab({ canDraft, canRemind }: { canDraft: boolean; canRemind: boolean }) {
  const [kind, setKind] = useState<"RECEIVABLE" | "PAYABLE">("RECEIVABLE");
  const [aging, setAging] = useState<Aging | null>(null);
  const [schedule, setSchedule] = useState<Schedule | null>(null);
  const [vouchers, setVouchers] = useState<Voucher[]>([]);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [paying, setPaying] = useState<{ workflowId: string; paidOn: string; reference: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "ok" | "error"; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const [agingResponse, scheduleResponse] = await Promise.all([
        api.get<Aging>("/api/v1/finance/reports/aging", { params: { kind } }),
        api.get<Schedule>("/api/v1/finance/reports/payment-schedule", { params: { horizon_days: 30 } }),
      ]);
      setAging(agingResponse.data);
      setSchedule(scheduleResponse.data);
      if (canDraft) {
        const { data } = await api.get<Voucher[]>("/api/v1/finance/payments/vouchers");
        setVouchers(data);
      }
    } catch (error) {
      setMessage({ tone: "error", text: apiError(error).message });
    }
  }, [kind, canDraft]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const run = async (action: () => Promise<string>) => {
    setBusy(true);
    setMessage(null);
    try {
      setMessage({ tone: "ok", text: await action() });
      await load();
    } catch (error) {
      setMessage({ tone: "error", text: apiError(error).message });
    } finally {
      setBusy(false);
    }
  };

  const draftVoucher = () => run(async () => {
    await api.post("/api/v1/finance/payments/vouchers", { invoice_ids: Array.from(picked) });
    setPicked(new Set());
    return "Đã lập phiếu chi nháp và gửi tới Trung tâm phê duyệt.";
  });

  const remind = (partyId: string, level: number) => run(async () => {
    await api.post("/api/v1/finance/reminders", { party_id: partyId, level });
    return "Đã soạn thư nhắc nợ và gửi duyệt. Thư chỉ được gửi khi người duyệt đồng ý.";
  });

  const markPaid = () => run(async () => {
    if (!paying) return "";
    await api.post(`/api/v1/finance/payments/vouchers/${paying.workflowId}/paid`, { paid_on: paying.paidOn, bank_reference: paying.reference });
    setPaying(null);
    return "Đã ghi nhận thanh toán và ghi sổ Nợ 331 / Có 112.";
  });

  const toggle = (id: string) => {
    const next = new Set(picked);
    if (next.has(id)) next.delete(id); else next.add(id);
    setPicked(next);
  };

  return (
    <div style={{ display: "grid", gap: 16 }}>
      {message && <div className="ta-card" style={{ padding: 12, color: message.tone === "error" ? "#B91C1C" : "#047857" }}>{message.text}</div>}

      <section className="ta-card" style={{ padding: 16 }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 10 }}>
          <strong style={{ marginRight: 8 }}>Tuổi nợ</strong>
          <button className={`ta-btn ${kind === "RECEIVABLE" ? "ta-btn-primary" : "ta-btn-ghost"}`} onClick={() => setKind("RECEIVABLE")}>Phải thu</button>
          <button className={`ta-btn ${kind === "PAYABLE" ? "ta-btn-primary" : "ta-btn-ghost"}`} onClick={() => setKind("PAYABLE")}>Phải trả</button>
          {aging && <span style={{ marginLeft: "auto" }}>Tổng: <b>{formatVnd(aging.total)}</b> tại {aging.as_of}</span>}
        </div>
        {!aging || aging.parties.length === 0 ? <small style={{ color: "var(--text-muted)" }}>Không có công nợ còn mở.</small> : (
          <div style={{ overflowX: "auto" }}>
            <table className="ta-table" style={{ width: "100%", fontSize: 13 }}>
              <thead>
                <tr><th>Đối tượng</th>{BUCKETS.map(([, label]) => <th key={label} style={num}>{label}</th>)}<th style={num}>Tổng</th>{kind === "RECEIVABLE" && canRemind && <th />}</tr>
              </thead>
              <tbody>
                {aging.parties.map((row) => (
                  <tr key={row.party_id || row.party}>
                    <td>{row.party}<br /><small style={{ color: "var(--text-muted)" }}>{row.tax_code}</small></td>
                    {BUCKETS.map(([key]) => <td key={String(key)} style={num}>{Number(row[key]) ? formatVnd(row[key] as string) : ""}</td>)}
                    <td style={{ ...num, fontWeight: 700 }}>{formatVnd(row.total)}</td>
                    {kind === "RECEIVABLE" && canRemind && row.party_id && (
                      <td>
                        <select className="ta-input" style={{ width: 150 }} disabled={busy} defaultValue=""
                          onChange={(event) => { if (event.target.value) void remind(row.party_id as string, Number(event.target.value)); event.target.value = ""; }}>
                          <option value="">Soạn nhắc nợ…</option>
                          <option value="1">Mức 1 – thông báo đến hạn</option>
                          <option value="2">Mức 2 – nhắc quá hạn</option>
                          <option value="3">Mức 3 – đề nghị lần cuối</option>
                        </select>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="ta-card" style={{ padding: 16 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
          <strong>Hoá đơn mua vào đến hạn trong 30 ngày</strong>
          {canDraft && (
            <button className="ta-btn ta-btn-primary" disabled={busy || picked.size === 0} onClick={() => void draftVoucher()}>
              {busy ? <Loader2 className="animate-spin" size={15} /> : <Banknote size={15} />} Lập phiếu chi ({picked.size})
            </button>
          )}
        </div>
        {!schedule || schedule.invoices.length === 0 ? <small style={{ color: "var(--text-muted)" }}>Không có hoá đơn đến hạn.</small> : (
          <table className="ta-table" style={{ width: "100%", fontSize: 13 }}>
            <thead><tr>{canDraft && <th />}<th>Nhà cung cấp</th><th>Hoá đơn</th><th>Hạn</th><th style={num}>Còn phải trả</th><th style={num}>Đã lên lịch</th></tr></thead>
            <tbody>
              {schedule.invoices.map((row) => (
                <tr key={row.invoice_id} style={{ color: row.overdue ? "#B91C1C" : undefined }}>
                  {canDraft && <td><input type="checkbox" checked={picked.has(row.invoice_id)} disabled={!Number(row.to_schedule)} onChange={() => toggle(row.invoice_id)} aria-label="Chọn hoá đơn" /></td>}
                  <td>{row.party}</td><td>{row.series} số {row.number}</td><td>{row.due_date}{row.overdue ? " (quá hạn)" : ""}</td>
                  <td style={num}>{formatVnd(row.remaining)}</td><td style={num}>{Number(row.already_scheduled) ? formatVnd(row.already_scheduled) : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {canDraft && <small style={{ display: "block", marginTop: 8, color: "var(--text-muted)" }}>Một phiếu chi trả cho một nhà cung cấp. Hệ thống không tự chuyển tiền.</small>}
      </section>

      {canDraft && vouchers.length > 0 && (
        <section className="ta-card" style={{ padding: 16 }}>
          <strong style={{ display: "block", marginBottom: 10 }}>Phiếu chi</strong>
          <table className="ta-table" style={{ width: "100%", fontSize: 13 }}>
            <thead><tr><th>Số phiếu</th><th>Hoá đơn</th><th style={num}>Số tiền</th><th>Trạng thái</th><th /></tr></thead>
            <tbody>
              {vouchers.map((voucher) => (
                <tr key={voucher.workflow_id}>
                  <td>{voucher.reference}</td><td>{voucher.invoice_count}</td><td style={num}>{formatVnd(voucher.total)}</td>
                  <td>{VOUCHER_STATUS[voucher.status] || voucher.status}</td>
                  <td>
                    {voucher.status === "SCHEDULED" && (
                      paying?.workflowId === voucher.workflow_id ? (
                        <span style={{ display: "flex", gap: 6 }}>
                          <input type="date" className="ta-input" style={{ width: 150 }} value={paying.paidOn} onChange={(event) => setPaying({ ...paying, paidOn: event.target.value })} />
                          <input className="ta-input" style={{ width: 150 }} placeholder="Mã giao dịch NH" value={paying.reference} onChange={(event) => setPaying({ ...paying, reference: event.target.value })} />
                          <button className="ta-btn ta-btn-primary" disabled={busy || paying.reference.trim().length < 2} onClick={() => void markPaid()}>Lưu</button>
                        </span>
                      ) : (
                        <button className="ta-btn ta-btn-ghost" onClick={() => setPaying({ workflowId: voucher.workflow_id, paidOn: new Date().toISOString().slice(0, 10), reference: "" })}>
                          <Mail size={14} /> Ghi nhận đã chuyển tiền
                        </button>
                      )
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}

const num: React.CSSProperties = { textAlign: "right", whiteSpace: "nowrap" };
