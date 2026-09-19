"use client";

import React from "react";
import {
  Laptop,
  AlertTriangle,
  FileCheck,
  Download,
  DollarSign,
  TrendingUp,
  Tag,
  ExternalLink,
} from "lucide-react";

// --- 1. Jira Ticket Card (IT Agent) ---
interface JiraTicketCardProps {
  ticketKey: string;
  summary: string;
  reporterName: string;
  priority: string;
  status: string;
  assignedTo: string;
}

export const JiraTicketCard: React.FC<JiraTicketCardProps> = ({
  ticketKey,
  summary,
  reporterName,
  priority,
  status,
  assignedTo,
}) => {
  return (
    <div
      style={{
        marginTop: "12px",
        padding: "16px 20px",
        borderRadius: "14px",
        background: "rgba(99, 102, 241, 0.08)",
        border: "1px solid rgba(99, 102, 241, 0.3)",
        backdropFilter: "blur(10px)",
        maxWidth: "480px",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "10px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px", color: "#818cf8", fontSize: "0.85rem", fontWeight: 700 }}>
          <Laptop size={18} />
          JIRA TICKET CREATED: {ticketKey}
        </div>
        <span
          style={{
            fontSize: "0.7rem",
            padding: "2px 8px",
            borderRadius: "99px",
            background: priority === "HIGH" ? "rgba(239,68,68,0.2)" : "rgba(245,158,11,0.2)",
            color: priority === "HIGH" ? "#ef4444" : "#f59e0b",
            fontWeight: 700,
          }}
        >
          {priority} PRIORITY
        </span>
      </div>

      <div style={{ fontSize: "0.83rem", color: "var(--text-secondary)", lineHeight: 1.5, marginBottom: "12px" }}>
        <p style={{ margin: "2px 0" }}><strong>Tóm tắt sự cố:</strong> {summary}</p>
        <p style={{ margin: "2px 0" }}><strong>Người báo cáo:</strong> {reporterName}</p>
        <p style={{ margin: "2px 0" }}><strong>Giao cho:</strong> {assignedTo}</p>
      </div>

      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", borderTop: "1px solid rgba(255,255,255,0.08)", paddingTop: "10px" }}>
        <span style={{ fontSize: "0.75rem", color: "#10b981", fontWeight: 600 }}>Status: {status}</span>
        <button
          onClick={() => alert(`Direct link to Jira ticket ${ticketKey}`)}
          style={{
            background: "none",
            border: "none",
            color: "#6366f1",
            fontSize: "0.78rem",
            fontWeight: 600,
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            gap: "4px",
          }}
        >
          Xem trên Jira <ExternalLink size={13} />
        </button>
      </div>
    </div>
  );
};


// --- 3. Invoice Audit Card (Finance Agent) ---
interface InvoiceAuditCardProps {
  id: string;
  poNumber: string;
  vendorName: string;
  invoiceAmount: string;
  poExpectedAmount: string;
  status: string;
  anomalies: string[];
}

export const InvoiceAuditCard: React.FC<InvoiceAuditCardProps> = ({
  id,
  poNumber,
  vendorName,
  invoiceAmount,
  poExpectedAmount,
  status,
  anomalies,
}) => {
  const isFlagged = status === "DISCREPANCY_FLAGGED" || anomalies.length > 0;

  return (
    <div
      style={{
        marginTop: "12px",
        padding: "16px 20px",
        borderRadius: "14px",
        background: isFlagged ? "rgba(245, 158, 11, 0.08)" : "rgba(16, 185, 129, 0.08)",
        border: "1px solid " + (isFlagged ? "rgba(245, 158, 11, 0.3)" : "rgba(16, 185, 129, 0.3)"),
        backdropFilter: "blur(10px)",
        maxWidth: "480px",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "10px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px", color: isFlagged ? "#f59e0b" : "#10b981", fontSize: "0.85rem", fontWeight: 700 }}>
          <DollarSign size={18} />
          ĐỐI SOÁT HÓA ĐƠN & PO ({poNumber})
        </div>
        <span
          style={{
            fontSize: "0.7rem",
            padding: "2px 8px",
            borderRadius: "999px",
            background: isFlagged ? "rgba(239,68,68,0.2)" : "rgba(16,185,129,0.2)",
            color: isFlagged ? "#ef4444" : "#10b981",
            fontWeight: 700,
          }}
        >
          {isFlagged ? "FLAGGED" : "MATCHED"}
        </span>
      </div>

      <div style={{ fontSize: "0.83rem", color: "var(--text-secondary)", lineHeight: 1.5, marginBottom: "12px" }}>
        <p style={{ margin: "2px 0" }}><strong>Nhà cung cấp:</strong> {vendorName}</p>
        <p style={{ margin: "2px 0" }}><strong>Số tiền hóa đơn:</strong> <strong style={{ color: "#ef4444" }}>{invoiceAmount}</strong></p>
        <p style={{ margin: "2px 0" }}><strong>Số tiền PO hệ thống:</strong> {poExpectedAmount}</p>
      </div>

      {anomalies.length > 0 && (
        <div style={{ padding: "10px", borderRadius: "8px", background: "rgba(239, 68, 68, 0.15)", marginBottom: "10px" }}>
          <div style={{ fontSize: "0.75rem", fontWeight: 700, color: "#ef4444", marginBottom: "4px" }}>
            ⚠️ Bất thường tài chính phát hiện:
          </div>
          {anomalies.map((a, i) => (
            <div key={i} style={{ fontSize: "0.73rem", color: "#fca5a5" }}>• {a}</div>
          ))}
        </div>
      )}
    </div>
  );
};


// --- 4. Sales Quote Card (Sales Agent) ---
interface QuoteItem {
  name: string;
  quantity: number;
  unit_price: string;
  total: string;
}

interface SalesQuoteCardProps {
  id: string;
  customerName: string;
  items: QuoteItem[];
  subtotal: string;
  discount: string;
  totalAmount: string;
  pdfUrl: string;
}

export const SalesQuoteCard: React.FC<SalesQuoteCardProps> = ({
  id,
  customerName,
  items,
  subtotal,
  discount,
  totalAmount,
  pdfUrl,
}) => {
  return (
    <div
      style={{
        marginTop: "12px",
        padding: "16px 20px",
        borderRadius: "14px",
        background: "rgba(16, 185, 129, 0.08)",
        border: "1px solid rgba(16, 185, 129, 0.3)",
        backdropFilter: "blur(10px)",
        maxWidth: "480px",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "10px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px", color: "#10b981", fontSize: "0.85rem", fontWeight: 700 }}>
          <TrendingUp size={18} />
          BẢNG BÁO GIÁ SẢN PHẨM ({id})
        </div>
      </div>

      <div style={{ fontSize: "0.82rem", color: "var(--text-secondary)", marginBottom: "10px" }}>
        Khách hàng: <strong>{customerName}</strong>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: "6px", marginBottom: "12px" }}>
        {items.map((it, i) => (
          <div key={i} style={{ display: "flex", justifyContent: "space-between", fontSize: "0.78rem", padding: "6px 8px", background: "rgba(0,0,0,0.2)", borderRadius: "6px" }}>
            <span>{it.quantity}x {it.name}</span>
            <strong>{it.total}</strong>
          </div>
        ))}
      </div>

      <div style={{ borderTop: "1px solid rgba(255,255,255,0.08)", paddingTop: "8px", fontSize: "0.8rem", color: "var(--text-secondary)", marginBottom: "14px" }}>
        <div style={{ display: "flex", justifyContent: "space-between" }}><span>Tạm tính:</span> <span>{subtotal}</span></div>
        <div style={{ display: "flex", justifyContent: "space-between", color: "#10b981" }}><span>Chiết khấu:</span> <span>-{discount}</span></div>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.95rem", fontWeight: 800, color: "white", marginTop: "4px" }}>
          <span>Tổng cộng:</span> <span style={{ color: "#10b981" }}>{totalAmount}</span>
        </div>
      </div>

      <a
        href={pdfUrl}
        target="_blank"
        rel="noopener noreferrer"
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: "6px",
          padding: "8px 14px",
          borderRadius: "8px",
          background: "linear-gradient(135deg, #10b981, #059669)",
          color: "white",
          fontSize: "0.8rem",
          fontWeight: 600,
          textDecoration: "none",
        }}
      >
        <Download size={15} /> Tải Báo Giá PDF Chính Thức
      </a>
    </div>
  );
};
