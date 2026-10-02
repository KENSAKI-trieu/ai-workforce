"use client";

import { AlertTriangle } from "lucide-react";
import { useState } from "react";
import { formatVnd } from "@/components/finance/format";

/**
 * What a Finance draft waiting for approval shows its approver.
 *
 * The amount and the permission it requires were written by the server; an approver can
 * only re-point a journal line to another account, or reword a reminder. A voucher is
 * approved or rejected as it is.
 */

interface JournalLine { line_no: number; account_code: string; account_name?: string; debit: string; credit: string }
interface VoucherInvoice { invoice_id: string; series: string; number: string; due_date?: string | null; amount: string }

export interface FinancePayload {
  amount?: string;
  required_permission_label?: string;
  // Journal entry
  invoice?: { series: string; number: string; issue_date?: string | null; party?: string; total_amount?: string };
  lines?: JournalLine[];
  proposed_by?: "RULE" | "MODEL" | "USER";
  confidence?: "HIGH" | "LOW";
  confidence_reasons?: string[];
  // Payment voucher
  party?: { name: string; tax_code?: string | null; bank_name?: string | null; bank_account?: string | null };
  invoices?: VoucherInvoice[];
  warnings?: string[];
  // Reminder
  recipient?: string;
  subject?: string;
  body?: string;
  delivery?: { status: string; error?: string };
}

const PROPOSED_BY: Record<string, string> = {
  RULE: "Theo luật hạch toán đã duyệt",
  MODEL: "Mô hình AI chọn tài khoản",
  USER: "Người đề xuất chỉ định tài khoản",
};

export function isFinanceApproval(actionType: string): boolean {
  return actionType.startsWith("FINANCE_");
}

export function financeEditable(actionType: string): boolean {
  return actionType === "FINANCE_JOURNAL_APPROVAL" || actionType === "FINANCE_REMINDER_SEND";
}

export default function FinanceApprovalDetail({
  actionType,
  payload,
  readOnly,
  onEdit,
}: {
  actionType: string;
  payload: FinancePayload;
  readOnly: boolean;
  /** The edited payload to send with "Sửa & duyệt": only the fields the server accepts. */
  onEdit: (edited: Record<string, unknown>) => void;
}) {
  const [accounts, setAccounts] = useState<Record<number, string>>({});
  const [subject, setSubject] = useState(payload.subject || "");
  const [body, setBody] = useState(payload.body || "");

  const setAccount = (lineNo: number, code: string) => {
    const next = { ...accounts, [lineNo]: code };
    setAccounts(next);
    onEdit({
      lines: (payload.lines || []).map((line) => ({
        line_no: line.line_no,
        account_code: (next[line.line_no] ?? line.account_code).trim(),
      })),
    });
  };

  const header = (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginBottom: 12, fontSize: 13 }}>
      {payload.amount && <span><b>Số tiền:</b> {formatVnd(payload.amount)}</span>}
      {payload.required_permission_label && <span><b>Cần quyền:</b> {payload.required_permission_label}</span>}
    </div>
  );

  if (actionType === "FINANCE_JOURNAL_APPROVAL") {
    return (
      <div style={box}>
        {header}
        {payload.invoice && (
          <p style={{ margin: "0 0 10px", fontSize: 13 }}>
            Hoá đơn <b>{payload.invoice.series} số {payload.invoice.number}</b>
            {payload.invoice.issue_date ? ` ngày ${payload.invoice.issue_date}` : ""} · {payload.invoice.party}
          </p>
        )}
        <p style={{ margin: "0 0 10px", fontSize: 13, color: "var(--text-muted)" }}>
          {PROPOSED_BY[payload.proposed_by || ""] || ""} · Độ tin cậy{" "}
          <span className={`ta-badge ${payload.confidence === "HIGH" ? "ta-badge-success" : "ta-badge-warning"}`}>
            {payload.confidence === "HIGH" ? "cao" : "thấp"}
          </span>
        </p>
        {(payload.confidence_reasons || []).length > 0 && (
          <ul style={warningList}>
            {(payload.confidence_reasons || []).map((reason) => (
              <li key={reason} style={{ display: "flex", gap: 6 }}><AlertTriangle size={14} style={{ flexShrink: 0, marginTop: 2 }} />{reason}</li>
            ))}
          </ul>
        )}
        <table className="ta-table" style={{ width: "100%", fontSize: 13 }}>
          <thead><tr><th>#</th><th>Tài khoản</th><th style={{ textAlign: "right" }}>Nợ</th><th style={{ textAlign: "right" }}>Có</th></tr></thead>
          <tbody>
            {(payload.lines || []).map((line) => (
              <tr key={line.line_no}>
                <td>{line.line_no}</td>
                <td>
                  {readOnly ? (
                    <span>{line.account_code} {line.account_name}</span>
                  ) : (
                    <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      <input
                        className="ta-input"
                        style={{ width: 90, padding: "4px 8px" }}
                        value={accounts[line.line_no] ?? line.account_code}
                        onChange={(event) => setAccount(line.line_no, event.target.value)}
                        aria-label={`Tài khoản dòng ${line.line_no}`}
                      />
                      <small style={{ color: "var(--text-muted)" }}>{line.account_name}</small>
                    </span>
                  )}
                </td>
                <td style={{ textAlign: "right" }}>{Number(line.debit) ? formatVnd(line.debit) : ""}</td>
                <td style={{ textAlign: "right" }}>{Number(line.credit) ? formatVnd(line.credit) : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!readOnly && <small style={{ display: "block", marginTop: 8, color: "var(--text-muted)" }}>
          Đổi số tài khoản rồi bấm “Sửa & duyệt”. Số tiền không sửa được. Duyệt nguyên trạng sẽ ghi nhớ tài khoản này cho đối tác.
        </small>}
      </div>
    );
  }

  if (actionType === "FINANCE_PAYMENT_VOUCHER") {
    return (
      <div style={box}>
        {header}
        {payload.party && (
          <p style={{ margin: "0 0 10px", fontSize: 13 }}>
            Trả cho <b>{payload.party.name}</b>{payload.party.tax_code ? ` (MST ${payload.party.tax_code})` : ""}
            {payload.party.bank_account ? ` · TK ${payload.party.bank_account}${payload.party.bank_name ? ` ${payload.party.bank_name}` : ""}` : ""}
          </p>
        )}
        {(payload.warnings || []).length > 0 && (
          <ul style={{ ...warningList, background: "#FEF2F2", color: "#991B1B" }}>
            {(payload.warnings || []).map((warning) => (
              <li key={warning} style={{ display: "flex", gap: 6 }}><AlertTriangle size={14} style={{ flexShrink: 0, marginTop: 2 }} />{warning}</li>
            ))}
          </ul>
        )}
        <table className="ta-table" style={{ width: "100%", fontSize: 13 }}>
          <thead><tr><th>Hoá đơn</th><th>Hạn</th><th style={{ textAlign: "right" }}>Số trả</th></tr></thead>
          <tbody>
            {(payload.invoices || []).map((invoice) => (
              <tr key={invoice.invoice_id}>
                <td>{invoice.series} số {invoice.number}</td>
                <td>{invoice.due_date || "—"}</td>
                <td style={{ textAlign: "right" }}>{formatVnd(invoice.amount)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <small style={{ display: "block", marginTop: 8, color: "var(--text-muted)" }}>
          Duyệt không chuyển tiền: người thực hiện chuyển khoản trên ngân hàng rồi ghi nhận đã trả ở trang Tài chính.
        </small>
      </div>
    );
  }

  if (actionType === "FINANCE_REMINDER_SEND") {
    return (
      <div style={box}>
        {header}
        <p style={{ margin: "0 0 8px", fontSize: 13 }}><b>Gửi tới:</b> {payload.recipient}</p>
        {payload.delivery && (
          <p style={{ margin: "0 0 8px", fontSize: 13, color: payload.delivery.status === "FAILED" ? "#B91C1C" : "#047857" }}>
            {payload.delivery.status === "FAILED" ? `Gửi thất bại: ${payload.delivery.error}` : "Đã gửi"}
          </p>
        )}
        <input
          className="ta-input"
          readOnly={readOnly}
          value={subject}
          onChange={(event) => { setSubject(event.target.value); onEdit({ subject: event.target.value, body }); }}
          style={{ marginBottom: 8 }}
          aria-label="Tiêu đề thư"
        />
        <textarea
          className="ta-input"
          rows={12}
          readOnly={readOnly}
          value={body}
          onChange={(event) => { setBody(event.target.value); onEdit({ subject, body: event.target.value }); }}
          style={{ fontSize: 13 }}
          aria-label="Nội dung thư"
        />
        {!readOnly && <small style={{ display: "block", marginTop: 6, color: "var(--text-muted)" }}>
          Có thể sửa lời văn; người nhận và số liệu giữ nguyên như bản soạn.
        </small>}
      </div>
    );
  }
  return null;
}

const box: React.CSSProperties = { padding: 14, border: "1px solid #DBE4EC", borderRadius: 9, background: "#F8FAFC" };
const warningList: React.CSSProperties = {
  margin: "0 0 10px", padding: "8px 10px", listStyle: "none", borderRadius: 6,
  background: "#FFFBEB", color: "#92400E", fontSize: 13, display: "grid", gap: 4,
};
