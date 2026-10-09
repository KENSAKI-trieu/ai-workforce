"use client";

import { AlertTriangle, ShieldCheck } from "lucide-react";
import { useState } from "react";

import { PLATFORM_LABELS, PLATFORMS, type Platform } from "./campaign";

/**
 * What a marketing content approval shows its approver: the three posts as they will be
 * published and what the fact-check said. The approver may correct a post while approving;
 * only the post texts are sent back, as `{drafts: {...}}`.
 */

export interface MarketingPayload {
  title?: string;
  requester_name?: string;
  marketing_campaign_id?: string;
  drafts?: Partial<Record<Platform, string>>;
  fact_check?: {
    passed?: boolean;
    checked?: boolean;
    summary?: string;
    open_issues?: number;
    edited_after_check?: string[];
  };
  edited_by_approver?: string[];
}

export function isMarketingApproval(actionType: string): boolean {
  return actionType === "MARKETING_CONTENT_APPROVAL";
}

export default function MarketingApprovalDetail({
  payload,
  readOnly,
  onEdit,
}: {
  payload: MarketingPayload;
  readOnly: boolean;
  onEdit: (edited: { drafts: Partial<Record<Platform, string>> }) => void;
}) {
  const original = payload.drafts ?? {};
  const [active, setActive] = useState<Platform>("facebook");
  const [drafts, setDrafts] = useState<Partial<Record<Platform, string>>>(original);
  const check = payload.fact_check ?? {};
  const edited = check.edited_after_check ?? [];

  const change = (text: string) => {
    const next = { ...drafts, [active]: text };
    setDrafts(next);
    const changed = PLATFORMS.filter((platform) => (next[platform] ?? "") !== (original[platform] ?? ""));
    onEdit({ drafts: Object.fromEntries(changed.map((platform) => [platform, next[platform] ?? ""])) });
  };

  return (
    <div style={{ padding: 14, border: "1px solid #DBE4EC", borderRadius: 9, background: "#F8FAFC" }}>
      <strong style={{ display: "block", marginBottom: 4 }}>{payload.title || "Nội dung truyền thông"}</strong>
      <small style={{ color: "var(--text-muted)" }}>Bài Facebook, Instagram, Threads do Trợ lý Marketing viết, người gửi đã duyệt từng bước.</small>
      <div style={{ display: "flex", gap: 8, alignItems: "flex-start", margin: "10px 0", padding: 9, borderRadius: 7, background: check.passed ? "#EEF9F2" : "#FFF7EC", color: check.passed ? "#1D7A4F" : "#8A4B08", fontSize: 13 }}>
        {check.passed ? <ShieldCheck size={15} /> : <AlertTriangle size={15} />}
        <span>
          {check.passed
            ? "Kiểm chứng số liệu: đạt."
            : check.checked === false
              ? "Kiểm chứng không đọc được kết quả, cần xem thủ công."
              : `Kiểm chứng còn ${check.open_issues ?? 0} vấn đề chưa sửa.`}
          {check.summary ? ` ${check.summary}` : ""}
          {edited.length > 0 && ` Người gửi đã sửa tay sau kiểm chứng: ${edited.map((name) => PLATFORM_LABELS[name as Platform] ?? name).join(", ")}.`}
        </span>
      </div>
      <div style={{ display: "flex", gap: 4, borderBottom: "1px solid #E2E8F0", marginBottom: 10 }}>
        {PLATFORMS.map((platform) => (
          <button
            key={platform}
            type="button"
            onClick={() => setActive(platform)}
            style={{ padding: "8px 11px", border: 0, borderBottom: `2px solid ${active === platform ? "#C2410C" : "transparent"}`, background: "transparent", color: active === platform ? "#9A3412" : "#64748B", fontWeight: 650, cursor: "pointer" }}
          >
            {PLATFORM_LABELS[platform]}
          </button>
        ))}
      </div>
      {readOnly
        ? <div style={{ whiteSpace: "pre-wrap", fontSize: 13, lineHeight: 1.6, color: "#334155" }}>{drafts[active] || "—"}</div>
        : (
          <textarea
            className="ta-input"
            rows={12}
            value={drafts[active] ?? ""}
            onChange={(event) => change(event.target.value)}
            aria-label={`Bài ${PLATFORM_LABELS[active]}`}
            style={{ fontSize: 13, lineHeight: 1.6 }}
          />
        )}
      {!readOnly && <small style={{ display: "block", marginTop: 6, color: "var(--text-muted)" }}>Sửa bài rồi bấm “Sửa & duyệt” để duyệt bản đã sửa.</small>}
    </div>
  );
}
