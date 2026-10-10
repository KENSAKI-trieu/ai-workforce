"use client";

import { AlertTriangle, CheckCircle2, Circle, Copy, Edit3, ExternalLink, Eye, Loader2, RotateCcw, Send, ShieldCheck, XCircle } from "lucide-react";
import { useState } from "react";

import ChatMessageContent from "@/components/chat/ChatMessageContent";
import type { StageStatus } from "@/lib/marketingStream";
import {
  type Campaign,
  type FactCheckReport,
  issuesByPlatform,
  overlongThreadPosts,
  PLATFORM_LABELS,
  PLATFORMS,
  type Platform,
  type Skill,
  type Source,
  STAGE_LABELS,
  STAGE_NAMES,
  STAGE_TONES,
} from "./campaign";
import styles from "./marketing.module.css";

export interface StageState { status: StageStatus; detail?: string | null }

/** The steps a request runs, ticked off as the server reports them. */
export function StagePipeline({ stages, state }: { stages: readonly string[]; state: Record<string, StageState> }) {
  return (
    <div className={styles.pipeline} aria-live="polite">
      {stages.map((stage) => {
        const current = state[stage] ?? { status: "pending" };
        const icon = current.status === "running"
          ? <Loader2 size={15} className={styles.spin} />
          : current.status === "done"
            ? <CheckCircle2 size={15} />
            : current.status === "failed" ? <XCircle size={15} /> : <Circle size={15} />;
        const tone = current.status === "running"
          ? styles.pipeRunning
          : current.status === "done" ? styles.pipeDone : current.status === "failed" ? styles.pipeFailed : "";
        return (
          <div key={stage} className={`${styles.pipeStep} ${tone}`}>
            {icon}<span>{STAGE_NAMES[stage] ?? stage}</span>{current.detail && <small>{current.detail}</small>}
          </div>
        );
      })}
    </div>
  );
}

export function StageBadge({ stage }: { stage: Campaign["stage"] }) {
  return <span className={`${styles.badge} ${styles[STAGE_TONES[stage]]}`}>{STAGE_LABELS[stage]}</span>;
}

/** The documents and web pages the outline cites as [1], [2] ... */
export function SourcesPanel({ sources }: { sources: Source[] }) {
  if (!sources.length) {
    return <p className={styles.phase}>Không tìm thấy tài liệu công ty hay trang web liên quan; dàn ý chỉ dựa trên brief, chỗ thiếu số liệu được đánh dấu “[cần bổ sung số liệu]”.</p>;
  }
  return (
    <div className={styles.sources}>
      {sources.map((source) => {
        const web = source.kind === "web";
        return (
          <details key={`${source.ref}-${source.chunk_id || source.url}`} className={styles.source}>
            <summary>
              <b>{source.ref}</b>
              {web && <i className={styles.webTag}>Web</i>}
              <span>{source.document_title}{web ? (source.site && source.site !== source.document_title ? ` — ${source.site}` : "") : source.section_title ? ` — ${source.section_title}` : ""}</span>
            </summary>
            <p>{source.content}</p>
            {web && source.url && (
              <a className={styles.sourceLink} href={source.url} target="_blank" rel="noopener noreferrer">
                <ExternalLink size={12} /> {source.url}
              </a>
            )}
          </details>
        );
      })}
    </div>
  );
}

/** The skill shelf the writing followed: guidance, so it is listed but never cited. */
export function SkillsPanel({ skills }: { skills: Skill[] }) {
  if (!skills.length) return null;
  return (
    <>
      <h3 style={{ margin: "18px 0 4px", fontSize: ".86rem" }}>Kỹ năng đã tham khảo</h3>
      <p className={styles.phase} style={{ marginTop: 0 }}>Dùng để định hướng cách viết, không phải nguồn số liệu nên không được trích dẫn.</p>
      <div className={styles.sources}>
        {skills.map((skill) => (
          <details key={skill.chunk_id} className={styles.source}>
            <summary><i className={styles.skillTag}>Kỹ năng</i><span>{skill.document_title}{skill.section_title ? ` — ${skill.section_title}` : ""}</span></summary>
            <p>{skill.content}</p>
          </details>
        ))}
      </div>
    </>
  );
}

type OutlineMode = "view" | "edit" | "reject";

/** The first stop: approve, edit or reject the outline (with the reason). */
export function OutlineReview({
  campaign,
  busy,
  onApprove,
  onEdit,
  onReject,
}: {
  campaign: Campaign;
  busy: boolean;
  onApprove: () => void;
  onEdit: (outline: string) => void;
  onReject: (feedback: string) => void;
}) {
  const outline = campaign.outline ?? "";
  const [mode, setMode] = useState<OutlineMode>("view");
  const [text, setText] = useState(outline);
  const [feedback, setFeedback] = useState("");
  const edited = text.trim() !== outline.trim();

  return (
    <section className={styles.panel}>
      <div className={styles.panelHeader}>
        <div><h2>Duyệt dàn ý chiến dịch</h2><p>Duyệt để trợ lý viết bài 3 kênh, sửa trực tiếp, hoặc từ chối kèm lý do để soạn lại.</p></div>
        <StageBadge stage={campaign.stage} />
      </div>
      <div className={styles.panelBody}>
        {campaign.outline_feedback && (
          <div className={styles.notice}><RotateCcw size={15} />Soạn lại theo góp ý: “{campaign.outline_feedback}”</div>
        )}
        {mode === "edit"
          ? <textarea className={`${styles.textarea} ${styles.mono}`} style={{ minHeight: 420 }} value={text} onChange={(event) => setText(event.target.value)} aria-label="Sửa dàn ý (Markdown)" autoFocus />
          : <div className={styles.outline}><ChatMessageContent content={outline} /></div>}
        {mode === "reject" && (
          <div style={{ marginTop: 14 }}>
            <label htmlFor="outline-feedback" className={styles.phase}>Lý do từ chối — trợ lý sẽ tra cứu lại và đề xuất hướng khác</label>
            <textarea
              id="outline-feedback"
              className={styles.textarea}
              value={feedback}
              maxLength={4000}
              onChange={(event) => setFeedback(event.target.value)}
              placeholder="VD: Tập trung vào kế toán trưởng doanh nghiệp nhỏ, bỏ phần influencer…"
              autoFocus
            />
          </div>
        )}
        <h3 style={{ margin: "18px 0 8px", fontSize: ".86rem" }}>Nguồn tham chiếu</h3>
        <SourcesPanel sources={campaign.sources ?? []} />
        <SkillsPanel skills={campaign.skills ?? []} />
      </div>
      <div className={styles.actions}>
        {mode === "view" && (
          <>
            <button className={styles.dangerButton} disabled={busy} onClick={() => setMode("reject")}><XCircle size={15} />Từ chối</button>
            <button className={styles.secondaryButton} disabled={busy} onClick={() => setMode("edit")}><Edit3 size={15} />Sửa trực tiếp</button>
            <button className={styles.primaryButton} disabled={busy} onClick={onApprove}><CheckCircle2 size={15} />Duyệt & viết bài</button>
          </>
        )}
        {mode === "edit" && (
          <>
            <button className={styles.secondaryButton} disabled={busy} onClick={() => { setMode("view"); setText(outline); }}>Huỷ sửa</button>
            <button className={styles.primaryButton} disabled={busy || !text.trim()} onClick={() => (edited ? onEdit(text) : onApprove())}>
              <CheckCircle2 size={15} />{edited ? "Lưu & viết bài" : "Duyệt (không đổi)"}
            </button>
          </>
        )}
        {mode === "reject" && (
          <>
            <button className={styles.secondaryButton} disabled={busy} onClick={() => setMode("view")}>Quay lại</button>
            <button className={styles.primaryButton} disabled={busy || !feedback.trim()} onClick={() => onReject(feedback.trim())}><RotateCcw size={15} />Gửi & soạn lại</button>
          </>
        )}
      </div>
    </section>
  );
}

/** The fact-check of the posts: what it found, the open issue of the platform in view first. */
export function FactCheckPanel({ report, focus }: { report: FactCheckReport | null; focus?: Platform }) {
  if (!report) return <p className={styles.phase}>Không có báo cáo kiểm chứng.</p>;
  const counts = issuesByPlatform(report);
  const issues = [...report.issues].sort((a, b) => Number(b.platform === focus) - Number(a.platform === focus));
  const edited = report.edited_platforms ?? [];
  return (
    <div>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8 }}>
        {report.passed
          ? <span className={`${styles.badge} ${styles.green}`}><ShieldCheck size={12} />Đạt</span>
          : report.checked === false
            ? <span className={`${styles.badge} ${styles.amber}`}><AlertTriangle size={12} />Cần kiểm tra thủ công</span>
            : <span className={`${styles.badge} ${styles.amber}`}><AlertTriangle size={12} />Còn {report.issues.length} vấn đề</span>}
        <span className={styles.phase}>Sau {report.round} lần tự sửa</span>
      </div>
      {report.summary && <p className={styles.phase} style={{ marginTop: 8 }}>{report.summary}</p>}
      <ul className={styles.platformList}>
        {PLATFORMS.map((platform) => (
          <li key={platform}>
            <span>{PLATFORM_LABELS[platform]}</span>
            {edited.includes(platform)
              ? <span className={`${styles.badge} ${styles.blue}`}>Đã sửa tay sau kiểm chứng</span>
              : counts[platform] === 0
                ? <span className={`${styles.badge} ${styles.green}`}>Đạt</span>
                : <span className={`${styles.badge} ${styles.red}`}>{counts[platform]} lỗi</span>}
          </li>
        ))}
      </ul>
      {issues.length > 0 && (
        <ol className={styles.issueList}>
          {issues.map((issue, index) => (
            <li key={index} className={`${styles.issue} ${focus && issue.platform !== focus ? styles.dim : ""}`}>
              <span className={`${styles.badge} ${issue.kind === "fact" ? styles.red : styles.amber}`}>{issue.kind === "fact" ? "Số liệu" : "Sáo rỗng"}</span>{" "}
              <small className={styles.phase} style={{ display: "inline" }}>{PLATFORM_LABELS[issue.platform as Platform] ?? "Chung"}</small>
              <p><q>{issue.claim}</q></p>
              <p>{issue.problem}</p>
              {issue.suggestion && <p>→ {issue.suggestion}</p>}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

/**
 * The posts, three tabs. While the campaign waits for its author (DRAFTS_PENDING, or back
 * after a rejection) they can be edited and settled; once settled they are read and copied.
 */
export function DraftsReview({
  campaign,
  busy,
  onSettle,
  onSubmit,
}: {
  campaign: Campaign;
  busy: boolean;
  onSettle: (updated: Partial<Record<Platform, string>> | null) => void;
  onSubmit: () => void;
}) {
  const original = campaign.drafts;
  const editable = campaign.stage === "DRAFTS_PENDING" || campaign.stage === "REJECTED";
  const [active, setActive] = useState<Platform>("facebook");
  const [editing, setEditing] = useState(false);
  const [drafts, setDrafts] = useState<Partial<Record<Platform, string>>>(original);
  const [copied, setCopied] = useState<Platform | null>(null);
  const changed = PLATFORMS.filter((platform) => (drafts[platform] ?? "") !== (original[platform] ?? ""));
  const counts = issuesByPlatform(campaign.fact_check_report);
  const text = drafts[active] ?? "";
  const tooLong = active === "threads" ? overlongThreadPosts(text) : 0;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(active);
      window.setTimeout(() => setCopied(null), 1500);
    } catch {
      setCopied(null);
    }
  };

  return (
    <section className={styles.panel}>
      <div className={styles.panelHeader}>
        <div>
          <h2>{editable ? "Duyệt bài viết đa kênh" : "Bài viết đã chốt"}</h2>
          <p>{editable ? "Đối chiếu kết quả kiểm chứng, sửa từng kênh nếu cần rồi chốt." : "Sao chép từng bài để đăng; trạng thái phê duyệt ở bên dưới."}</p>
        </div>
        <StageBadge stage={campaign.stage} />
      </div>
      <div className={styles.reviewGrid}>
        <div style={{ minWidth: 0 }}>
          <div className={styles.tabs} role="tablist">
            {PLATFORMS.map((platform) => (
              <button
                key={platform}
                role="tab"
                aria-selected={active === platform}
                className={active === platform ? styles.activeTab : ""}
                onClick={() => setActive(platform)}
              >
                {PLATFORM_LABELS[platform]}
                {changed.includes(platform) ? <small>• đã sửa</small> : counts[platform] ? <small>({counts[platform]})</small> : null}
              </button>
            ))}
          </div>
          <div className={styles.panelBody}>
            {editing && editable
              ? <textarea className={`${styles.textarea} ${styles.mono}`} style={{ minHeight: 320 }} value={text} onChange={(event) => setDrafts({ ...drafts, [active]: event.target.value })} aria-label={`Sửa bài ${PLATFORM_LABELS[active]}`} />
              : <div className={styles.post}>{text || "—"}</div>}
            <div className={styles.meta}>
              <span>{[...text].length} ký tự</span>
              {tooLong > 0 && <span className={`${styles.badge} ${styles.amber}`}>{tooLong} bài vượt 500 ký tự</span>}
              {changed.includes(active) && <button type="button" className={styles.linkButton} onClick={() => setDrafts({ ...drafts, [active]: original[active] })}>Hoàn tác kênh này</button>}
              {!editable && <button type="button" className={styles.linkButton} onClick={() => void copy()}><Copy size={11} /> {copied === active ? "Đã sao chép" : "Sao chép bài"}</button>}
            </div>
          </div>
        </div>
        <aside>
          <h3 style={{ margin: "0 0 8px", fontSize: ".86rem" }}>Kiểm chứng</h3>
          <FactCheckPanel report={campaign.fact_check_report} focus={active} />
        </aside>
      </div>
      <div className={styles.actions}>
        {editable && (
          <>
            {changed.length > 0 && <span className={styles.grow}>Đã sửa {changed.length} kênh</span>}
            <button className={styles.secondaryButton} disabled={busy} onClick={() => setEditing(!editing)}>
              {editing ? <><Eye size={15} />Xem trước</> : <><Edit3 size={15} />Sửa trực tiếp</>}
            </button>
            <button
              className={styles.primaryButton}
              disabled={busy}
              onClick={() => onSettle(changed.length ? Object.fromEntries(changed.map((platform) => [platform, drafts[platform] ?? ""])) : null)}
            >
              <CheckCircle2 size={15} />{changed.length ? "Lưu chỉnh sửa & chốt" : "Duyệt & chốt bài"}
            </button>
          </>
        )}
        {campaign.stage === "FINAL" && (
          <>
            <span className={styles.grow}>Bài đã chốt. Gửi để người có quyền duyệt nội dung xem trước khi đăng.</span>
            <button className={styles.primaryButton} disabled={busy} onClick={onSubmit}><Send size={15} />Gửi phê duyệt</button>
          </>
        )}
        {campaign.stage === "SUBMITTED" && <span className={styles.grow}>Đang chờ phê duyệt ở Trung tâm phê duyệt.</span>}
        {campaign.stage === "APPROVED" && <span className={styles.grow}>Nội dung đã được duyệt, có thể đăng.</span>}
      </div>
    </section>
  );
}
