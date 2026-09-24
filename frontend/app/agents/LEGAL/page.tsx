"use client";

import axios from "axios";
import {
  AlertTriangle,
  ArrowRight,
  BadgeCheck,
  Bot,
  CalendarClock,
  Check,
  CheckCircle2,
  ChevronRight,
  CircleGauge,
  Code2,
  FileDiff,
  FileKey2,
  FilePlus2,
  FileSearch,
  FileText,
  Fingerprint,
  GitCompareArrows,
  Loader2,
  LockKeyhole,
  MessageSquareText,
  Search,
  Send,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Upload,
  UsersRound,
  WandSparkles,
  X,
  Download,
  ExternalLink,
  Eye,
} from "lucide-react";
import { FormEvent, RefObject, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import ChatMessageContent from "@/components/chat/ChatMessageContent";
import LegalDocumentGeneratorModal from "@/components/legal/LegalDocumentGeneratorModal";
import api from "@/lib/api";
import { useAuthStore } from "@/store/useAuthStore";
import styles from "./legal.module.css";

type View = "overview" | "chat" | "review" | "drafts" | "compliance" | "saved";
type ReviewMode = "contract" | "compare" | "privacy" | "license";
type Severity = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
type RepresentedParty = "" | "PARTY_A" | "PARTY_B" | "NEUTRAL";

interface Agent { name: string; description?: string; is_active: boolean; }
interface DocumentItem {
  document_id: string;
  document_name: string;
  document_title?: string;
  collection_name: string;
  department_access: string;
  confidentiality?: string;
  status: string;
  processing_status?: string;
  expiration_date?: string | null;
  chunk_count: number;
}
interface ApprovalItem { id: string; action_type: string; risk_level: Severity | "CRITICAL"; workflow_title: string; }
interface Citation { document_name?: string; section_title?: string; citation_tag?: string; }
// The slot-filling card the agent sends while it still needs something from the
// user: which party they act for, or whether the text is even meant to be reviewed.
interface ContractReviewDraftCard {
  type: "CONTRACT_REVIEW_DRAFT";
  status: "COLLECTING" | "AWAITING_INTENT" | "CANCELLED" | "DISMISSED";
  represented_party: string | null;
  contract_fingerprint: string;
  contract_char_count: number;
  contract_excerpt: string;
}
// A finished review arrives as the analyzer output itself, which carries no `type`.
type LegalRiskCard = ContractReviewDraftCard | ContractReview;
interface ChatResult {
  reply: string;
  citations: Citation[];
  conversation_id: string;
  legal_risk_card?: LegalRiskCard | null;
}
interface LegalChatMessage {
  role: "USER" | "ASSISTANT";
  content: string;
  citations?: Citation[];
  card?: LegalRiskCard | null;
}
interface LegalDraft {
  artifact_id: string;
  approval_id: string;
  workflow_id: string;
  document_type: string;
  document_type_label: string;
  filename: string;
  output_format: string;
  status: "WAITING" | "APPROVED" | "REJECTED" | "EXPIRED";
  comments?: string | null;
  requester_name: string;
  submitted_at?: string | null;
  updated_at?: string | null;
  can_preview: boolean;
  can_download: boolean;
  preview_url: string;
  download_url: string;
}
interface LegalDraftPreview { artifact_id: string; filename: string; document_type_label: string; status: string; requester_name: string; content: string; }
type DecisionVerb = "ACCEPTED" | "REJECTED" | "EDITED";
interface ReviewDecision {
  finding_key: string;
  decision: DecisionVerb;
  revised_text?: string | null;
  comment?: string | null;
  decided_by_name?: string | null;
  updated_at?: string | null;
}
interface SavedReviewSummary {
  review_id: string;
  document_name: string;
  contract_type_label?: string | null;
  represented_party_label?: string | null;
  risk_score: number;
  risk_level: Severity;
  total_findings: number;
  decided_count: number;
  accepted_count: number;
  status: string;
  source: string;
  created_by_name?: string | null;
  created_at?: string | null;
  redline_ready: boolean;
  redline_url: string;
}
interface RiskFinding {
  id: string;
  // Content-derived and stable across re-runs, unlike `id`, which is positional.
  // Decisions are keyed on this so they cannot reattach to a different finding.
  finding_key: string;
  clause: string;
  clause_title: string;
  severity: Severity;
  category: string;
  finding_type: "LEGAL_ISSUE" | "COMMERCIAL_RISK" | "POLICY_VIOLATION" | "MISSING_CLAUSE" | "AMBIGUOUS_CLAUSE" | "INTERNAL_CONFLICT";
  issue: string;
  reason: string;
  recommendation: string;
  evidence: string;
  original_text: string;
  suggested_revision: string;
  perspective: string;
  impact: "ADVERSE" | "BENEFICIAL" | "BALANCED" | "SHARED";
  sources: Array<{ id: string; title: string; type: string; url?: string; note?: string }>;
}
interface ContractReview {
  review_version: string;
  document_name: string;
  risk_score: number;
  risk_level: Severity;
  total_risks_found: number;
  risks: RiskFinding[];
  findings: RiskFinding[];
  represented_party: Exclude<RepresentedParty, "">;
  represented_party_label: string;
  contract_type: string;
  contract_type_label: string;
  contract_type_confidence: number;
  metadata: {
    contract_title: string; party_a: string; party_b: string;
    party_a_label: string; party_b_label: string; party_a_role: "COMPANY"; party_b_role: "CUSTOMER";
    party_a_source: "DOCUMENT" | "SYSTEM_DEFAULT"; party_b_source: "DOCUMENT" | "SYSTEM_DEFAULT";
    party_mapping_source: "DOCUMENT" | "DOCUMENT_AND_SYSTEM_DEFAULT" | "SYSTEM_DEFAULT";
    party_mapping_warnings: string[];
    contract_value?: string | null; dates: string[]; start_date?: string | null;
    end_date?: string | null; payment_terms: string[]; term?: string | null; clause_count: number;
  };
  checklist: Array<{ category: string; label: string; status: "PRESENT" | "MISSING" | "NOT_IN_EXCERPT"; severity_if_missing: Severity }>;
  severity_counts: Record<Severity, number>;
  missing_clauses_count: number;
  internal_conflicts_count: number;
  policy_violations_count: number;
  category_summary: Array<{ category: string; severity: Severity }>;
  reference_sources: Array<{ id: string; title: string; type: string; url?: string; reader_url?: string; note?: string; citation_tag?: string; section_title?: string }>;
  review_disclaimer: string;
  approval_created: boolean;
  workflow_id?: string | null;
  review_id: string;
  decisions?: ReviewDecision[];
  redline_url?: string;
}
interface PrivacyResult {
  document_name: string;
  contains_sensitive_data: boolean;
  requires_legal_approval: boolean;
  risk_level: Severity;
  findings: Array<{ type: string; count: number | null; severity: Severity }>;
  frameworks: string[];
  suggested_action: string;
  approval_created?: boolean;
}
interface CompareResult {
  old_document: string;
  new_document: string;
  similarity_percent: number;
  total_changes: number;
  changes: Array<{ type: string; old: string[]; new: string[]; old_location?: number | null; new_location?: number | null }>;
}
interface LicenseResult {
  manifest: string;
  dependencies_scanned: number;
  risk_level: Severity;
  commercial_use_requires_review: boolean;
  unresolved_dependencies: number;
  findings: Array<{ package: string; license: string; severity: Severity; action: string }>;
  approval_created?: boolean;
}
interface DocumentReader {
  document_id?: string;
  document_name: string;
  document_title: string;
  document_type?: string;
  version?: string;
  content: string;
  character_count?: number;
  chunk_count?: number;
  source_url?: string | null;
  download_url?: string | null;
}

const REVIEW_MODES: Array<{ id: ReviewMode; label: string; icon: typeof FileSearch; accept: string }> = [
  { id: "contract", label: "Rà soát hợp đồng", icon: FileSearch, accept: ".pdf,.docx,.txt,.md,.csv" },
  { id: "compare", label: "So sánh phiên bản", icon: FileDiff, accept: ".pdf,.docx,.txt,.md" },
  { id: "privacy", label: "Kiểm tra dữ liệu", icon: Fingerprint, accept: ".xlsx,.csv,.json,.txt,.pdf,.docx" },
  { id: "license", label: "License phần mềm", icon: Code2, accept: ".json,.txt" },
];

function messageFrom(error: unknown) {
  if (!axios.isAxiosError(error)) return "Không thể xử lý yêu cầu.";
  const detail = error.response?.data?.detail;
  return typeof detail === "string" ? detail : error.message;
}

function isDraftCard(card: LegalRiskCard): card is ContractReviewDraftCard {
  return (card as ContractReviewDraftCard).type === "CONTRACT_REVIEW_DRAFT";
}

function riskClass(level: string) {
  if (level === "HIGH" || level === "CRITICAL") return styles.high;
  if (level === "MEDIUM") return styles.medium;
  return styles.low;
}

export default function LegalAgentPage() {
  const router = useRouter();
  const { isAuthenticated, hasHydrated, user } = useAuthStore();
  const [view, setView] = useState<View>("overview");
  const [agent, setAgent] = useState<Agent | null>(null);
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [approvals, setApprovals] = useState<ApprovalItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [chatMessages, setChatMessages] = useState<LegalChatMessage[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [reviewMode, setReviewMode] = useState<ReviewMode>("contract");
  const [primaryFile, setPrimaryFile] = useState<File | null>(null);
  const [secondaryFile, setSecondaryFile] = useState<File | null>(null);
  const [representedParty, setRepresentedParty] = useState<RepresentedParty>("");
  const [reviewResult, setReviewResult] = useState<ContractReview | PrivacyResult | CompareResult | LicenseResult | null>(null);
  const [drafts, setDrafts] = useState<LegalDraft[]>([]);
  const [savedReviews, setSavedReviews] = useState<SavedReviewSummary[]>([]);
  const [draftPreview, setDraftPreview] = useState<LegalDraftPreview | null>(null);
  const [draftNotice, setDraftNotice] = useState<string | null>(null);
  const [showGenerator, setShowGenerator] = useState(false);
  const questionRef = useRef<HTMLInputElement | null>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [agentResponse, documentsResponse, approvalsResponse, draftsResponse, reviewsResponse] = await Promise.all([
        api.get<Agent>("/api/v1/agents/LEGAL"),
        api.get<DocumentItem[]>("/api/v1/documents"),
        api.get<ApprovalItem[]>("/api/v1/approvals/pending"),
        api.get<LegalDraft[]>("/api/v1/legal/document-drafts"),
        api.get<SavedReviewSummary[]>("/api/v1/legal/contract-reviews"),
      ]);
      setAgent(agentResponse.data);
      setDocuments(documentsResponse.data);
      setApprovals(approvalsResponse.data.filter((item) => item.action_type.includes("LEGAL")));
      setDrafts(draftsResponse.data);
      setSavedReviews(reviewsResponse.data);
    } catch (reason) {
      setError(messageFrom(reason));
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
    const timer = window.setTimeout(() => void loadData(), 0);
    return () => window.clearTimeout(timer);
  }, [hasHydrated, isAuthenticated, loadData, router]);

  const legalDocuments = useMemo(
    () => documents.filter((item) => ["LEGAL", "ALL"].includes(item.department_access)),
    [documents],
  );
  const readyDocuments = legalDocuments.filter((item) => item.processing_status === "ready" || item.status === "active");
  const deadlines = legalDocuments
    .filter((item) => item.expiration_date)
    .sort((a, b) => String(a.expiration_date).localeCompare(String(b.expiration_date)))
    .slice(0, 4);

  const sendToLegal = async (content: string) => {
    if (!content || busy) return;
    setBusy(true);
    setError(null);
    setChatMessages((current) => [...current, { role: "USER", content }]);
    setQuestion("");
    try {
      const { data } = await api.post<ChatResult>("/api/v1/agent/chat", {
        agent_role: "LEGAL",
        message: content,
        conversation_id: conversationId || undefined,
      });
      setConversationId(data.conversation_id);
      const card = data.legal_risk_card ?? null;
      setChatMessages((current) => [
        ...current,
        { role: "ASSISTANT", content: data.reply, citations: data.citations, card },
      ]);
      // A review run in chat is stored server-side exactly like an uploaded one, so
      // refresh the saved list: that is where its decisions and redline live.
      if (card && !isDraftCard(card)) await loadData();
    } catch (reason) {
      setError(messageFrom(reason));
    } finally {
      setBusy(false);
    }
  };

  const askLegal = (event?: FormEvent) => {
    event?.preventDefault();
    void sendToLegal(question.trim());
  };

  const openTool = (mode: ReviewMode) => {
    setReviewMode(mode);
    setReviewResult(null);
    setPrimaryFile(null);
    setSecondaryFile(null);
    setRepresentedParty("");
    setView(mode === "privacy" || mode === "license" ? "compliance" : "review");
  };

  const analyzeFiles = async () => {
    if (!primaryFile || (reviewMode === "compare" && !secondaryFile) || (reviewMode === "contract" && !representedParty)) return;
    setBusy(true);
    setError(null);
    setReviewResult(null);
    try {
      const form = new FormData();
      let endpoint = "/api/v1/legal/review-document";
      if (reviewMode === "compare") {
        form.append("old_file", primaryFile);
        form.append("new_file", secondaryFile!);
        endpoint = "/api/v1/legal/compare-documents";
      } else {
        form.append("file", primaryFile);
        if (reviewMode === "contract") form.append("represented_party", representedParty);
        if (reviewMode === "privacy") endpoint = "/api/v1/legal/privacy-check";
        if (reviewMode === "license") endpoint = "/api/v1/legal/license-check";
      }
      const { data } = await api.post(endpoint, form);
      setReviewResult(data);
      if ((data as ContractReview).approval_created) await loadData();
    } catch (reason) {
      setError(messageFrom(reason));
    } finally {
      setBusy(false);
    }
  };

  // Takes an id rather than a list row: chat reaches the same panel through the
  // review_id on its risk card, so accept/reject and the redline are one screen away
  // from the conversation instead of being unreachable from it.
  const openSavedReview = async (reviewId: string) => {
    setBusy(true);
    setError(null);
    try {
      const { data } = await api.get<ContractReview>(
        `/api/v1/legal/contract-reviews/${reviewId}`,
      );
      setReviewMode("contract");
      setReviewResult(data);
      setView("review");
    } catch (reason) {
      setError(messageFrom(reason));
    } finally {
      setBusy(false);
    }
  };

  const downloadDraft = async (draft: LegalDraft) => {
    if (!draft.can_download) return;
    setError(null);
    try {
      const response = await api.get<Blob>(draft.download_url, { responseType: "blob" });
      const url = URL.createObjectURL(response.data);
      const link = document.createElement("a");
      link.href = url;
      link.download = draft.status === "APPROVED" ? `approved-${draft.filename}` : draft.filename;
      link.click();
      URL.revokeObjectURL(url);
    } catch (reason) {
      setError(messageFrom(reason));
    }
  };

  const previewDraft = async (draft: LegalDraft) => {
    setBusy(true);
    setError(null);
    try {
      const { data } = await api.get<LegalDraftPreview>(draft.preview_url);
      setDraftPreview(data);
    } catch (reason) {
      setError(messageFrom(reason));
    } finally {
      setBusy(false);
    }
  };

  if (!hasHydrated || !isAuthenticated) return null;

  return (
    <div className={styles.page}>
      <Sidebar agentStatuses={{ LEGAL: agent?.is_active !== false }} />
      <div className={styles.shell}>
        <header className={styles.topbar}>
          <div className={styles.breadcrumb}><span>AI Employees</span><ChevronRight size={14} /><strong>Legal Agent</strong></div>
          <div className={styles.topActions}>
            <button type="button" className={styles.iconButton} title="Trung tâm phê duyệt" onClick={() => router.push("/approvals")}><ShieldCheck size={17} /></button>
            <span className={styles.status}><span />{agent?.is_active === false ? "Tạm dừng" : "Đang hoạt động"}</span>
          </div>
        </header>

        <div className={styles.workspace}>
          <aside className={styles.rail}>
            <div className={styles.agentIdentity}>
              <div className={styles.agentMark}><ShieldCheck size={23} /></div>
              <div><strong>Legal Counsel AI</strong><span>Pháp chế doanh nghiệp</span></div>
            </div>
            <nav className={styles.nav} aria-label="Legal workspace">
              <button className={view === "overview" ? styles.active : ""} onClick={() => setView("overview")}><CircleGauge size={17} />Tổng quan</button>
              <button className={view === "chat" ? styles.active : ""} onClick={() => setView("chat")}><MessageSquareText size={17} />Chat</button>
              <button className={view === "review" ? styles.active : ""} onClick={() => { setReviewMode("contract"); setReviewResult(null); setView("review"); }}><FileSearch size={17} />Tài liệu & hợp đồng</button>
              <button className={view === "saved" ? styles.active : ""} onClick={() => { void loadData(); setView("saved"); }}><FileText size={17} />Rà soát đã lưu</button>
              <button className={view === "drafts" ? styles.active : ""} onClick={() => setView("drafts")}><FilePlus2 size={17} />Tạo văn bản</button>
              <button className={view === "compliance" ? styles.active : ""} onClick={() => { setReviewMode("privacy"); setReviewResult(null); setView("compliance"); }}><ShieldAlert size={17} />Compliance & IP</button>
            </nav>
          </aside>

          <main className={styles.main}>
            {error && <div className={styles.error}><AlertTriangle size={17} /><span>{error}</span><button onClick={() => setError(null)} title="Đóng"><X size={15} /></button></div>}

            {view === "chat" && <ChatWorkspace messages={chatMessages} question={question} setQuestion={setQuestion} busy={busy} send={askLegal} onQuickReply={(text) => void sendToLegal(text)} onOpenReview={(reviewId) => void openSavedReview(reviewId)} inputRef={questionRef} onNewChat={() => { setChatMessages([]); setConversationId(null); setQuestion(""); }} />}

            {view === "overview" && (
              <>
                <div className={styles.headingRow}><div><span className={styles.eyebrow}>LEGAL OPERATIONS</span><h1>Trung tâm pháp chế</h1></div><button className={styles.primaryButton} onClick={() => setView("drafts")}><FilePlus2 size={16} />Tạo văn bản</button></div>
                <section className={styles.stats}>
                  <article><span className={styles.statIcon}><FileText size={18} /></span><div><strong>{loading ? "—" : legalDocuments.length}</strong><small>Tài liệu được phép xem</small></div><span className={styles.trend}>ACL</span></article>
                  <article><span className={`${styles.statIcon} ${styles.amber}`}><ShieldAlert size={18} /></span><div><strong>{approvals.length}</strong><small>Chờ phê duyệt Legal</small></div><button onClick={() => router.push("/approvals")} title="Mở phê duyệt"><ArrowRight size={15} /></button></article>
                  <article><span className={`${styles.statIcon} ${styles.green}`}><BadgeCheck size={18} /></span><div><strong>{readyDocuments.length}</strong><small>Đã lập chỉ mục RAG</small></div><span className={styles.trend}>Ready</span></article>
                  <article><span className={`${styles.statIcon} ${styles.red}`}><CalendarClock size={18} /></span><div><strong>{deadlines.length}</strong><small>Mốc hết hạn sắp tới</small></div><button onClick={() => router.push("/calendar")} title="Mở lịch"><ArrowRight size={15} /></button></article>
                </section>

                <div className={styles.dashboardGrid}>
                  <section className={styles.sectionBlock}>
                    <div className={styles.sectionHeader}><div><h2>Công cụ pháp lý</h2><p>Chọn nghiệp vụ cần xử lý</p></div></div>
                    <div className={styles.toolGrid}>
                      <Tool icon={FileSearch} title="Contract Review" meta="Risk · Summary · Clauses" tone="blue" onClick={() => openTool("contract")} />
                      <Tool icon={FileKey2} title="NDA Checker" meta="Sharing · Duration · Scope" tone="violet" onClick={() => openTool("contract")} />
                      <Tool icon={GitCompareArrows} title="Clause Comparison" meta="V1 / V2 · Changed terms" tone="cyan" onClick={() => openTool("compare")} />
                      <Tool icon={Fingerprint} title="Privacy Checker" meta="PII · PDPL · Approval" tone="rose" onClick={() => openTool("privacy")} />
                      <Tool icon={Code2} title="OSS & License" meta="GPL · AGPL · MIT · Apache" tone="green" onClick={() => openTool("license")} />
                      <Tool icon={WandSparkles} title="Contract Generator" meta="Tạo · Gửi duyệt · Tải bản cuối" tone="amber" onClick={() => setView("drafts")} />
                      <Tool icon={MessageSquareText} title="Chat Legal Agent" meta="Hỏi đáp · Citation · Lịch sử phiên" tone="blue" onClick={() => setView("chat")} />
                    </div>
                  </section>

                  <aside className={styles.deadlinePanel}>
                    <div className={styles.sectionHeader}><div><h2>Deadline pháp lý</h2><p>Hết hạn hợp đồng, NDA và license</p></div><button onClick={() => router.push("/calendar")}><CalendarClock size={15} /></button></div>
                    <div className={styles.deadlineList}>
                      {deadlines.length === 0 && <div className={styles.emptyCompact}><CheckCircle2 size={20} /><span>Chưa có deadline trong tài liệu được phép xem</span></div>}
                      {deadlines.map((item) => <div key={item.document_id}><span className={styles.dateBox}>{new Date(item.expiration_date!).getDate()}<small>{new Date(item.expiration_date!).toLocaleString("vi-VN", { month: "short" })}</small></span><div><strong>{item.document_title || item.document_name}</strong><small>{item.collection_name}</small></div><ChevronRight size={15} /></div>)}
                    </div>
                  </aside>
                </div>

                <section className={styles.pipeline}>
                  <div className={styles.sectionHeader}><div><h2>Legal RAG pipeline</h2><p>Authorization được thực thi trước mọi truy vấn dữ liệu</p></div><span className={styles.secureBadge}><ShieldCheck size={13} />Tenant isolated</span></div>
                  <div className={styles.pipelineSteps}>
                    {[ [LockKeyhole, "ACL Filter", "Role · Department · Owner"], [Search, "Hybrid Search", "pgvector + BM25"], [Sparkles, "Reranker", "BGE / Qwen"], [Bot, "Legal LLM", "Analysis · Citation"], [UsersRound, "Human Gate", "High-risk approval"] ].map(([Icon, title, sub], index) => <div key={String(title)}><span><Icon size={17} /></span><strong>{String(title)}</strong><small>{String(sub)}</small>{index < 4 && <ChevronRight className={styles.pipelineArrow} size={15} />}</div>)}
                  </div>
                </section>
              </>
            )}

            {(view === "review" || view === "compliance") && (
              <ReviewWorkspace
                mode={reviewMode}
                setMode={(nextMode) => { setReviewMode(nextMode); setReviewResult(null); setPrimaryFile(null); setSecondaryFile(null); setRepresentedParty(""); }}
                primaryFile={primaryFile}
                secondaryFile={secondaryFile}
                setPrimaryFile={setPrimaryFile}
                setSecondaryFile={setSecondaryFile}
                representedParty={representedParty}
                setRepresentedParty={setRepresentedParty}
                result={reviewResult}
                busy={busy}
                analyze={analyzeFiles}
              />
            )}

            {view === "saved" && <SavedReviewWorkspace reviews={savedReviews} busy={busy} open={(item) => void openSavedReview(item.review_id)} startReview={() => openTool("contract")} />}

            {view === "drafts" && <DraftWorkspace drafts={drafts} role={user?.role || "Employee"} notice={draftNotice} openGenerator={() => { setDraftNotice(null); setShowGenerator(true); }} preview={(draft) => void previewDraft(draft)} download={(draft) => void downloadDraft(draft)} openApprovals={() => router.push("/approvals")} busy={busy} />}
          </main>
        </div>
      </div>

      {showGenerator && <LegalDocumentGeneratorModal onClose={() => setShowGenerator(false)} onSubmitted={() => { setDraftNotice("Đã tạo bản nháp và gửi tới CEO/Admin/Owner phê duyệt."); setView("drafts"); void loadData(); }} />}
      {draftPreview && <DraftPreviewModal preview={draftPreview} onClose={() => setDraftPreview(null)} />}
    </div>
  );
}

function ChatWorkspace({ messages, question, setQuestion, busy, send, onQuickReply, onOpenReview, inputRef, onNewChat }: {
  messages: LegalChatMessage[]; question: string; setQuestion: (value: string) => void; busy: boolean;
  send: (event?: FormEvent) => void; onQuickReply: (text: string) => void;
  onOpenReview: (reviewId: string) => void;
  inputRef: RefObject<HTMLInputElement | null>; onNewChat: () => void;
}) {
  return <section className={styles.chatWorkspace}>
    <header><div><span><Bot size={19} /></span><div><strong>Chat với Legal Counsel AI</strong><small>Hỏi đáp pháp lý trong phạm vi cấu hình và quyền truy cập của Agent</small></div></div><button type="button" onClick={onNewChat}><MessageSquareText size={14} />Cuộc trò chuyện mới</button></header>
    <div className={styles.chatMessages}>
      {messages.length === 0 && <div className={styles.chatEmpty}><span><ShieldCheck size={28} /></span><h2>Tôi có thể hỗ trợ gì về pháp lý?</h2><p>Hỏi về hợp đồng, quy trình, policy hoặc tài liệu mà bạn được cấp quyền truy cập.</p><div>{["Tóm tắt nghĩa vụ trong hợp đồng", "Giải thích điều khoản chấm dứt", "Policy công ty quy định thế nào?"].map((suggestion) => <button key={suggestion} onClick={() => { setQuestion(suggestion); inputRef.current?.focus(); }}>{suggestion}</button>)}</div></div>}
      {messages.map((message, index) => {
        // The last card is the only live one: answering an older perspective question
        // would send a reply the agent has already moved past.
        const isLatest = index === messages.length - 1;
        return <article key={`${message.role}-${index}`} className={`${message.role === "USER" ? styles.userMessage : styles.agentMessage} ${message.card ? styles.cardMessage : ""}`}>
          <span>{message.role === "USER" ? "Bạn" : <Bot size={15} />}</span>
          <div>{message.role === "ASSISTANT" ? <ChatMessageContent content={message.content} /> : <p>{message.content}</p>}{message.card && <ChatRiskCard card={message.card} live={isLatest && !busy} onQuickReply={onQuickReply} onOpenReview={onOpenReview} />}{message.citations && message.citations.length > 0 && <footer>{message.citations.map((citation, citationIndex) => <span key={citationIndex}><FileText size={11} />{citation.citation_tag || citation.document_name}{citation.section_title ? ` · ${citation.section_title}` : ""}</span>)}</footer>}</div>
        </article>;
      })}
      {busy && <article className={styles.agentMessage}><span><Bot size={15} /></span><div className={styles.typing}><i /><i /><i /></div></article>}
    </div>
    <form className={styles.chatComposer} onSubmit={send}><input ref={inputRef} value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="Nhập câu hỏi cho Legal Agent…" /><button type="submit" disabled={busy || !question.trim()}>{busy ? <Loader2 className={styles.spin} size={17} /> : <Send size={17} />}</button></form>
  </section>;
}

const PERSPECTIVE_REPLIES = [
  { label: "Bên A", message: "Bên A", hint: "công ty / nhà cung cấp / bên bán" },
  { label: "Bên B", message: "Bên B", hint: "khách hàng / bên mua" },
  { label: "Trung lập", message: "trung lập", hint: "đánh giá khách quan" },
];

// The chat risk card. Without it the agent answered "mỗi phát hiện bên dưới" with
// nothing below: findings, the escalation and the link to the saved review were all
// computed, stored and then dropped by the client.
function ChatRiskCard({ card, live, onQuickReply, onOpenReview }: {
  card: LegalRiskCard; live: boolean;
  onQuickReply: (text: string) => void; onOpenReview: (reviewId: string) => void;
}) {
  if (isDraftCard(card)) {
    if (card.status === "CANCELLED" || card.status === "DISMISSED") {
      return <p className={styles.chatCardClosed}>
        <X size={12} />
        {card.status === "CANCELLED"
          ? "Đã hủy rà soát — không nội dung nào được phân tích hay lưu lại."
          : "Yêu cầu rà soát đang treo đã được đóng."}
      </p>;
    }
    const collecting = card.status === "COLLECTING";
    return <section className={styles.chatCard}>
      <header>
        <span className={styles.chatCardMark}><FileSearch size={15} /></span>
        <div>
          <strong>{collecting ? "Chờ chọn góc nhìn rà soát" : "Chưa rõ bạn muốn rà soát hay hỏi"}</strong>
          <small>{card.contract_char_count.toLocaleString("vi-VN")} ký tự · chưa phân tích, chưa lưu</small>
        </div>
      </header>
      {card.contract_excerpt && <blockquote className={styles.chatCardExcerpt}>{card.contract_excerpt}…</blockquote>}
      <div className={styles.chatQuickReplies}>
        {collecting
          ? PERSPECTIVE_REPLIES.map((item) => <button key={item.message} type="button" disabled={!live} title={item.hint} onClick={() => onQuickReply(item.message)}>{item.label}</button>)
          : <>
            <button type="button" disabled={!live} onClick={() => onQuickReply("rà soát")}>Rà soát nội dung này</button>
            <button type="button" disabled={!live} onClick={() => onQuickReply("Đừng rà soát, tôi chỉ hỏi thôi")}>Tôi chỉ hỏi thôi</button>
          </>}
        {collecting && <button type="button" className={styles.chatCancelReply} disabled={!live} onClick={() => onQuickReply("hủy")}>Hủy</button>}
      </div>
    </section>;
  }

  const findings = card.findings || card.risks || [];
  const preview = findings.slice(0, 3);
  return <section className={`${styles.chatCard} ${styles.chatReviewCard}`}>
    <header>
      <span className={`${styles.chatScore} ${riskClass(card.risk_level)}`}><strong>{card.risk_score}</strong><small>/100</small></span>
      <div>
        <span className={`${styles.riskBadge} ${riskClass(card.risk_level)}`}>{card.risk_level} RISK</span>
        <strong>{card.total_risks_found} phát hiện</strong>
        <small>{card.contract_type_label} · {card.represented_party_label}</small>
      </div>
    </header>
    <div className={styles.chatSeverities}>
      {(["CRITICAL", "HIGH", "MEDIUM", "LOW"] as Severity[]).map((level) => <span key={level}><b className={riskClass(level)}>{card.severity_counts?.[level] || 0}</b>{level}</span>)}
    </div>
    {card.approval_created && <p className={styles.chatCardAlert}><ShieldAlert size={13} />Đã tạo approval workflow — Legal phải duyệt trước khi ký.</p>}
    <ul className={styles.chatFindings}>
      {preview.map((item) => <li key={item.id}>
        <span className={`${styles.severityDot} ${riskClass(item.severity)}`} />
        <div>
          <strong>{item.clause === "MISSING" ? item.issue : `Điều ${item.clause} · ${item.issue}`}</strong>
          <small>{item.recommendation}</small>
        </div>
      </li>)}
    </ul>
    {findings.length > preview.length && <small className={styles.chatMoreFindings}>+{findings.length - preview.length} phát hiện nữa trong bản đầy đủ</small>}
    {card.review_id && <div className={styles.chatCardActions}>
      <button type="button" className={styles.primaryButton} onClick={() => onOpenReview(card.review_id)}><FileSearch size={13} />Mở bản rà soát đầy đủ</button>
      <small>Accept · Reject · tải redline ở màn hình đầy đủ</small>
    </div>}
  </section>;
}

function DraftWorkspace({ drafts, role, notice, openGenerator, preview, download, openApprovals, busy }: {
  drafts: LegalDraft[]; role: string; notice: string | null; openGenerator: () => void;
  preview: (draft: LegalDraft) => void; download: (draft: LegalDraft) => void; openApprovals: () => void; busy: boolean;
}) {
  const reviewer = ["Owner", "Admin", "CEO"].includes(role);
  return <>
    <div className={styles.headingRow}><div><span className={styles.eyebrow}>DOCUMENT APPROVAL WORKFLOW</span><h1>Tạo văn bản</h1><p>Tạo bản nháp, gửi phê duyệt và nhận bản cuối có kiểm soát.</p></div><button className={styles.primaryButton} onClick={openGenerator}><FilePlus2 size={16} />Tạo văn bản mới</button></div>
    {notice && <div className={styles.draftNotice}><CheckCircle2 size={17} /><span>{notice}</span></div>}
    <section className={styles.draftFlow}>
      {[ [FilePlus2, "1. Tạo bản nháp", "Người dùng nhập dữ liệu"], [Send, "2. Gửi phê duyệt", "Tự động tạo approval"], [ShieldCheck, "3. Executive review", "CEO · Admin · Owner"], [Download, "4. Tải bản cuối", "Mở sau khi APPROVED"] ].map(([Icon, title, text], index) => <div key={String(title)}><span><Icon size={16} /></span><div><strong>{String(title)}</strong><small>{String(text)}</small></div>{index < 3 && <ChevronRight size={14} />}</div>)}
    </section>
    <section className={styles.draftPanel}>
      <header><div><h2>{reviewer ? "Văn bản trong tenant" : "Văn bản của tôi"}</h2><p>{drafts.length} yêu cầu · quyền tải được kiểm tra ở server</p></div>{reviewer && <button onClick={openApprovals}><ShieldAlert size={14} />Mở Trung tâm phê duyệt</button>}</header>
      {drafts.length === 0 ? <div className={styles.draftEmpty}><FileText size={30} /><strong>Chưa có văn bản nào</strong><p>Tạo văn bản đầu tiên để bắt đầu workflow phê duyệt.</p></div> : <div className={styles.draftList}>{drafts.map((draft) => <article key={draft.artifact_id}>
        <span className={styles.draftFileIcon}><FileText size={18} /></span>
        <div className={styles.draftInfo}><strong>{draft.document_type_label}</strong><small>{draft.filename} · {draft.requester_name}</small><em>{draft.submitted_at ? new Date(draft.submitted_at).toLocaleString("vi-VN") : ""}</em>{draft.comments && <p>Nhận xét: {draft.comments}</p>}</div>
        <span className={`${styles.draftStatus} ${styles[`draft${draft.status}`]}`}>{draft.status === "WAITING" ? "Chờ duyệt" : draft.status === "APPROVED" ? "Đã duyệt" : draft.status === "REJECTED" ? "Từ chối" : "Hết hạn"}</span>
        <div className={styles.draftActions}><button disabled={busy || !draft.can_preview} onClick={() => preview(draft)}><Eye size={13} />Xem</button><button disabled={!draft.can_download} onClick={() => download(draft)} title={!draft.can_download ? "Chỉ tải được sau khi được phê duyệt" : "Tải văn bản"}><Download size={13} />{draft.status === "APPROVED" ? "Tải bản cuối" : "Tải bản nháp"}</button></div>
      </article>)}</div>}
    </section>
  </>;
}

function SavedReviewWorkspace({ reviews, busy, open, startReview }: {
  reviews: SavedReviewSummary[]; busy: boolean;
  open: (review: SavedReviewSummary) => void; startReview: () => void;
}) {
  return <>
    <div className={styles.headingRow}><div><span className={styles.eyebrow}>CONTRACT REVIEW HISTORY</span><h1>Rà soát đã lưu</h1><p>Mở lại bản rà soát cũ kèm quyết định đã đánh dấu và tải file redline.</p></div><button className={styles.primaryButton} onClick={startReview}><FileSearch size={16} />Rà soát hợp đồng mới</button></div>
    <section className={styles.draftPanel}>
      <header><div><h2>{reviews.length} bản rà soát</h2><p>Quyền xem được kiểm tra ở server</p></div></header>
      {reviews.length === 0 ? <div className={styles.draftEmpty}><FileSearch size={30} /><strong>Chưa có bản rà soát nào</strong><p>Kết quả rà soát hợp đồng sẽ được lưu lại tại đây.</p></div> : <div className={styles.draftList}>{reviews.map((item) => <article key={item.review_id}>
        <span className={styles.draftFileIcon}><FileSearch size={18} /></span>
        <div className={styles.draftInfo}>
          <strong>{item.document_name}</strong>
          <small>{item.contract_type_label || item.source} · {item.represented_party_label || "—"} · {item.created_by_name || ""}</small>
          <em>{item.created_at ? new Date(item.created_at).toLocaleString("vi-VN") : ""}</em>
        </div>
        <span className={`${styles.riskBadge} ${riskClass(item.risk_level)}`}>{item.risk_score}/100 {item.risk_level}</span>
        <span className={styles.draftStatus}>{item.decided_count}/{item.total_findings} đã xử lý</span>
        <div className={styles.draftActions}><button disabled={busy} onClick={() => open(item)}><Eye size={13} />Mở lại</button></div>
      </article>)}</div>}
    </section>
  </>;
}

function DraftPreviewModal({ preview, onClose }: { preview: LegalDraftPreview; onClose: () => void }) {
  return <div className={styles.readerBackdrop} role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className={styles.readerModal} role="dialog" aria-modal="true" aria-label="Xem trước văn bản pháp lý"><header><div><span><FileText size={19} /></span><div><strong>{preview.document_type_label}</strong><small>{preview.filename} · {preview.requester_name} · {preview.status}</small></div></div><button onClick={onClose}><X size={17} /></button></header><pre className={styles.readerContent}>{preview.content}</pre><footer><button onClick={onClose}>Đóng</button></footer></section></div>;
}

function Tool({ icon: Icon, title, meta, tone, onClick }: { icon: typeof FileSearch; title: string; meta: string; tone: string; onClick: () => void }) {
  return <button className={styles.tool} onClick={onClick}><span className={`${styles.toolIcon} ${styles[tone]}`}><Icon size={20} /></span><span><strong>{title}</strong><small>{meta}</small></span><ChevronRight size={16} /></button>;
}

function ReviewWorkspace({ mode, setMode, primaryFile, secondaryFile, setPrimaryFile, setSecondaryFile, representedParty, setRepresentedParty, result, busy, analyze }: {
  mode: ReviewMode; setMode: (mode: ReviewMode) => void; primaryFile: File | null; secondaryFile: File | null;
  setPrimaryFile: (file: File | null) => void; setSecondaryFile: (file: File | null) => void;
  representedParty: RepresentedParty; setRepresentedParty: (party: RepresentedParty) => void;
  result: ContractReview | PrivacyResult | CompareResult | LicenseResult | null; busy: boolean; analyze: () => void;
}) {
  const config = REVIEW_MODES.find((item) => item.id === mode)!;
  return <>
    <div className={styles.headingRow}><div><span className={styles.eyebrow}>DOCUMENT INTELLIGENCE</span><h1>Tài liệu & tuân thủ</h1></div></div>
    <div className={styles.segmented}>{REVIEW_MODES.map((item) => <button key={item.id} className={mode === item.id ? styles.selected : ""} onClick={() => { setMode(item.id); setPrimaryFile(null); setSecondaryFile(null); }}><item.icon size={15} />{item.label}</button>)}</div>
    <div className={styles.reviewGrid}>
      <section className={styles.uploadPanel}>
        <div className={styles.sectionHeader}><div><h2>{config.label}</h2><p>{mode === "compare" ? "Chọn bản cũ và bản mới" : "PDF, DOCX, TXT, CSV, XLSX hoặc JSON · tối đa 10 MB"}</p></div></div>
        {mode === "contract" && <div className={styles.partySelector}>
          <strong>Bạn đang đại diện cho bên nào?</strong>
          <small>Góc nhìn này ảnh hưởng trực tiếp đến mức rủi ro của từng điều khoản.</small>
          <div>
            {([ ["PARTY_A", "Bên A · Công ty"], ["PARTY_B", "Bên B · Khách hàng"], ["NEUTRAL", "Neutral review"] ] as const).map(([value, label]) =>
              <button key={value} type="button" className={representedParty === value ? styles.partySelected : ""} onClick={() => setRepresentedParty(value)}>{label}</button>
            )}
          </div>
        </div>}
        <FilePicker label={mode === "compare" ? "Hợp đồng V1" : "Tài liệu cần kiểm tra"} file={primaryFile} setFile={setPrimaryFile} accept={config.accept} />
        {mode === "compare" && <FilePicker label="Hợp đồng V2" file={secondaryFile} setFile={setSecondaryFile} accept={config.accept} />}
        <button className={styles.analyzeButton} disabled={busy || !primaryFile || (mode === "compare" && !secondaryFile) || (mode === "contract" && !representedParty)} onClick={analyze}>{busy ? <Loader2 className={styles.spin} size={17} /> : <Sparkles size={17} />}{busy ? "Đang phân tích…" : "Phân tích tài liệu"}</button>
        <div className={styles.securityNote}><LockKeyhole size={15} /><span><strong>Xử lý trong tenant của doanh nghiệp</strong><small>Không gửi nội dung sang dịch vụ ngoài workflow được phê duyệt.</small></span></div>
      </section>
      <section className={styles.resultPanel}>
        {!result && <div className={styles.resultEmpty}><FileSearch size={35} /><h3>Chưa có kết quả phân tích</h3><p>Kết quả, evidence, mức rủi ro và hành động đề xuất sẽ xuất hiện tại đây.</p></div>}
        {result && <ResultView result={result} />}
      </section>
    </div>
  </>;
}

function FilePicker({ label, file, setFile, accept }: { label: string; file: File | null; setFile: (file: File | null) => void; accept: string }) {
  const ref = useRef<HTMLInputElement | null>(null);
  return <div className={`${styles.filePicker} ${file ? styles.hasFile : ""}`} onClick={() => ref.current?.click()}>
    <input ref={ref} type="file" accept={accept} onChange={(event) => setFile(event.target.files?.[0] || null)} />
    <span>{file ? <FileText size={21} /> : <Upload size={21} />}</span>
    <div><strong>{file?.name || label}</strong><small>{file ? `${(file.size / 1024).toFixed(0)} KB · Sẵn sàng` : "Bấm để chọn file"}</small></div>
    {file ? <Check size={17} /> : <ChevronRight size={17} />}
  </div>;
}

function ResultView({ result }: { result: ContractReview | PrivacyResult | CompareResult | LicenseResult }) {
  if ("risk_score" in result) return <ContractReviewResult review={result} />;
  if ("similarity_percent" in result) return <div className={styles.compareResult}><header><span><FileDiff size={20} /></span><div><h2>{result.total_changes} thay đổi</h2><p>Tương đồng {result.similarity_percent}% · {result.old_document} → {result.new_document}</p></div></header><div className={styles.changes}>{result.changes.map((change, index) => <article key={index}><span className={styles.changeType}>{change.type}</span>{change.old.map((line, i) => <p className={styles.oldLine} key={`old-${i}`}>− {line}</p>)}{change.new.map((line, i) => <p className={styles.newLine} key={`new-${i}`}>+ {line}</p>)}</article>)}</div></div>;
  if ("contains_sensitive_data" in result) return <div className={styles.privacyResult}><header><span className={result.requires_legal_approval ? styles.dangerMark : styles.safeMark}>{result.requires_legal_approval ? <ShieldAlert size={22} /> : <ShieldCheck size={22} />}</span><div><span className={`${styles.riskBadge} ${riskClass(result.risk_level)}`}>{result.risk_level}</span><h2>{result.requires_legal_approval ? "Cần Legal phê duyệt" : "Không phát hiện dữ liệu nhạy cảm"}</h2><p>{result.document_name}</p></div></header>{result.approval_created && <div className={styles.workflowAlert}><ShieldAlert size={17} /><span><strong>Đã tạo approval workflow</strong><small>Employee → Manager → Legal Team</small></span></div>}<div className={styles.piiGrid}>{result.findings.map((item) => <div key={item.type}><Fingerprint size={15} /><span><strong>{item.type}</strong><small>{item.count == null ? "Phát hiện theo cột" : `${item.count} giá trị`}</small></span></div>)}</div><div className={styles.actionNote}><strong>Hành động đề xuất</strong><p>{result.suggested_action}</p></div></div>;
  return <div className={styles.licenseResult}><header><span><Code2 size={21} /></span><div><span className={`${styles.riskBadge} ${riskClass(result.risk_level)}`}>{result.risk_level}</span><h2>{result.dependencies_scanned} dependency đã quét</h2><p>{result.manifest}</p></div></header>{result.commercial_use_requires_review && <div className={styles.workflowAlert}><AlertTriangle size={17} /><span><strong>{result.approval_created ? "Đã tạo approval workflow" : "Cần kiểm tra license thương mại"}</strong><small>Không phát hành trước khi Legal xác nhận.</small></span></div>}<div className={styles.licenseList}>{result.findings.map((item) => <article key={`${item.package}-${item.license}`}><span className={`${styles.licenseTag} ${riskClass(item.severity)}`}>{item.license}</span><div><strong>{item.package}</strong><small>{item.action}</small></div></article>)}</div></div>;
}

function ContractReviewResult({ review }: { review: ContractReview }) {
  // Keyed by finding_key, not by the positional id, and seeded from what the server
  // already has so reopening a review shows the decisions taken earlier.
  const savedDecisions = useMemo(() => {
    const initial: Record<string, DecisionVerb> = {};
    const texts: Record<string, string> = {};
    for (const item of review.decisions || []) {
      initial[item.finding_key] = item.decision;
      if (item.revised_text) texts[item.finding_key] = item.revised_text;
    }
    return { initial, texts };
  }, [review.decisions]);

  const [decisions, setDecisions] = useState<Record<string, DecisionVerb>>(savedDecisions.initial);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>(savedDecisions.texts);
  const [saving, setSaving] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [reader, setReader] = useState<DocumentReader | null>(null);
  const [readerLoading, setReaderLoading] = useState(false);
  const [readerError, setReaderError] = useState<string | null>(null);
  const metadata = review.metadata;
  const acceptedCount = Object.values(decisions).filter(
    (value) => value === "ACCEPTED" || value === "EDITED",
  ).length;
  const findingTypeLabels: Record<RiskFinding["finding_type"], string> = {
    LEGAL_ISSUE: "Vấn đề pháp lý", COMMERCIAL_RISK: "Rủi ro thương mại",
    POLICY_VIOLATION: "Vi phạm policy", MISSING_CLAUSE: "Điều khoản thiếu",
    AMBIGUOUS_CLAUSE: "Điều khoản mơ hồ", INTERNAL_CONFLICT: "Mâu thuẫn nội bộ",
  };

  // Optimistic, then rolled back if the server refuses: a decision that only ever
  // lived in this component was the whole defect being fixed here.
  const saveDecision = async (
    findingKey: string,
    decision: DecisionVerb,
    revisedText?: string,
  ) => {
    const previous = decisions[findingKey];
    setDecisions((current) => ({ ...current, [findingKey]: decision }));
    setSaving(findingKey);
    setSaveError(null);
    try {
      await api.put(
        `/api/v1/legal/contract-reviews/${review.review_id}/decisions/${findingKey}`,
        { decision, revised_text: revisedText ?? null },
      );
    } catch (reason) {
      setDecisions((current) => {
        const rolledBack = { ...current };
        if (previous) rolledBack[findingKey] = previous;
        else delete rolledBack[findingKey];
        return rolledBack;
      });
      setSaveError(messageFrom(reason));
    } finally {
      setSaving(null);
    }
  };

  const setDecision = (findingKey: string, decision: "ACCEPTED" | "REJECTED") => {
    setEditingId(null);
    void saveDecision(findingKey, decision);
  };

  const downloadRedline = async () => {
    setSaveError(null);
    try {
      const response = await api.get<Blob>(
        review.redline_url || `/api/v1/legal/contract-reviews/${review.review_id}/redline`,
        { responseType: "blob" },
      );
      const objectUrl = URL.createObjectURL(response.data);
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = `redline-${review.document_name.replace(/\.[^.]+$/, "")}.docx`;
      link.click();
      URL.revokeObjectURL(objectUrl);
    } catch (reason) {
      setSaveError(messageFrom(reason));
    }
  };

  const openReference = async (source: ContractReview["reference_sources"][number]) => {
    if (source.url) {
      window.open(source.url, "_blank", "noopener,noreferrer");
      return;
    }
    setReaderError(null);
    if (!source.reader_url) {
      setReader({
        document_name: source.title,
        document_title: source.title,
        document_type: source.type,
        content: source.note || "Nguồn này là rule pack hệ thống và chưa có file gốc trong Knowledge Base.",
      });
      return;
    }
    setReaderLoading(true);
    try {
      const { data } = await api.get<DocumentReader>(source.reader_url);
      setReader(data);
    } catch (reason) {
      setReaderError(messageFrom(reason));
    } finally {
      setReaderLoading(false);
    }
  };

  const downloadOriginal = async () => {
    if (!reader?.download_url) return;
    setReaderError(null);
    try {
      const response = await api.get<Blob>(reader.download_url, { responseType: "blob" });
      const objectUrl = URL.createObjectURL(response.data);
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = reader.document_name;
      link.click();
      URL.revokeObjectURL(objectUrl);
    } catch (reason) {
      setReaderError(messageFrom(reason));
    }
  };

  return <div className={styles.contractResult}>
    <header><div className={`${styles.scoreRing} ${riskClass(review.risk_level)}`}><strong>{review.risk_score}</strong><small>/100</small></div><div><span className={`${styles.riskBadge} ${riskClass(review.risk_level)}`}>{review.risk_level} RISK</span><h2>{review.document_name}</h2><p>{review.contract_type_label} · {review.represented_party_label} · {review.total_risks_found} phát hiện</p></div></header>
    <div className={styles.contractScroll}>
      {review.approval_created && <div className={styles.workflowAlert}><ShieldAlert size={17} /><span><strong>Đã tạo approval workflow</strong><small>Employee → Manager → Legal Team</small></span></div>}
      {saveError && <div className={styles.workflowAlert}><AlertTriangle size={17} /><span><strong>Không lưu được quyết định</strong><small>{saveError}</small></span></div>}

      <section className={styles.reviewSection}>
        <div className={styles.reviewSectionTitle}>
          <div>
            <strong>Bản rà soát đã được lưu</strong>
            <small>
              {acceptedCount > 0
                ? `${acceptedCount}/${review.findings.length} đề xuất đã được chấp nhận · redline sẵn sàng`
                : "Chấp nhận ít nhất một đề xuất để xuất file redline"}
            </small>
          </div>
          <button
            type="button"
            className={styles.primaryButton}
            disabled={acceptedCount === 0}
            title={acceptedCount === 0 ? "Cần ít nhất một đề xuất được chấp nhận" : "Tải báo cáo redline"}
            onClick={() => void downloadRedline()}
          >
            <Download size={15} />Tải redline
          </button>
        </div>
      </section>

      <section className={styles.reviewSection}>
        <div className={styles.reviewSectionTitle}><div><strong>Tóm tắt hợp đồng</strong><small>Loại hợp đồng được nhận diện với độ tin cậy {Math.round(review.contract_type_confidence * 100)}%</small></div></div>
        <div className={styles.metadataGrid}>
          <div><small>Tên hợp đồng</small><strong>{metadata.contract_title}</strong></div>
          <div><small>{metadata.party_a_label}</small><strong>{metadata.party_a}</strong><em>{metadata.party_a_source === "DOCUMENT" ? "Theo tài liệu" : "Hệ thống tự định nghĩa"}</em></div>
          <div><small>{metadata.party_b_label}</small><strong>{metadata.party_b}</strong><em>{metadata.party_b_source === "DOCUMENT" ? "Theo tài liệu" : "Hệ thống tự định nghĩa"}</em></div>
          <div><small>Giá trị</small><strong>{metadata.contract_value || "Chưa trích xuất"}</strong></div>
          <div><small>Thời hạn</small><strong>{metadata.start_date || "—"} → {metadata.end_date || "—"}</strong></div>
          <div><small>Cấu trúc</small><strong>{metadata.clause_count} điều khoản</strong></div>
        </div>
        {metadata.party_mapping_warnings.length > 0 && <div className={styles.partyWarning}><AlertTriangle size={14} /><div><strong>Cần xác nhận vai trò các bên</strong>{metadata.party_mapping_warnings.map((warning) => <p key={warning}>{warning}</p>)}</div></div>}
        {metadata.payment_terms.length > 0 && <div className={styles.paymentSummary}><small>Điều kiện thanh toán</small><p>{metadata.payment_terms[0]}</p></div>}
      </section>

      <section className={styles.reviewSection}>
        <div className={styles.reviewSectionTitle}><div><strong>Tổng quan rủi ro</strong><small>Điểm được tính từ mức độ và có floor theo finding nghiêm trọng nhất</small></div></div>
        <div className={styles.riskMetrics}>
          {(["CRITICAL", "HIGH", "MEDIUM", "LOW"] as Severity[]).map((level) => <div key={level}><strong className={riskClass(level)}>{review.severity_counts[level] || 0}</strong><small>{level}</small></div>)}
          <div><strong>{review.missing_clauses_count}</strong><small>Thiếu điều khoản</small></div>
          <div><strong>{review.internal_conflicts_count}</strong><small>Mâu thuẫn</small></div>
          <div><strong>{review.policy_violations_count}</strong><small>Vi phạm policy</small></div>
        </div>
        <p className={styles.scoringNote}>Cách tính: CRITICAL +35 · HIGH +22 · MEDIUM +10 · LOW +4. Nếu có một finding HIGH, điểm tổng tối thiểu là 70; chỉ finding CRITICAL mới làm báo cáo mang nhãn CRITICAL.</p>
        <div className={styles.categorySummary}>{review.category_summary.map((item) => <span key={item.category}>{item.category}<b className={riskClass(item.severity)}>{item.severity}</b></span>)}</div>
      </section>

      <section className={styles.reviewSection}>
        <div className={styles.reviewSectionTitle}><div><strong>Checklist theo loại hợp đồng</strong><small>{review.checklist.filter((item) => item.status === "PRESENT").length}/{review.checklist.length} nhóm điều khoản hiện diện</small></div></div>
        <div className={styles.checklist}>{review.checklist.map((item) => <div key={item.category} className={item.status === "MISSING" ? styles.missingItem : ""}>{item.status === "PRESENT" ? <CheckCircle2 size={14} /> : <AlertTriangle size={14} />}<span>{item.label}</span><small>{item.status === "PRESENT" ? "Có" : item.status === "NOT_IN_EXCERPT" ? "Ngoài đoạn trích" : `Thiếu · ${item.severity_if_missing}`}</small></div>)}</div>
      </section>

      <section className={`${styles.reviewSection} ${styles.findingSection}`}>
        <div className={styles.reviewSectionTitle}><div><strong>Phát hiện & đề xuất sửa</strong><small>AI không thay đổi file gốc; mỗi đề xuất cần được xác nhận</small></div></div>
        <div className={styles.findings}>{review.findings.map((item) => {
          const decision = decisions[item.finding_key];
          const draft = drafts[item.finding_key] ?? item.suggested_revision;
          return <article key={item.id}>
            <div className={styles.findingHeader}><span className={`${styles.severityDot} ${riskClass(item.severity)}`} /><strong>{item.clause === "MISSING" ? item.issue : `Điều ${item.clause} · ${item.issue}`}</strong><span className={`${styles.impactBadge} ${styles[item.impact.toLowerCase()]}`}>{item.impact === "ADVERSE" ? "Bất lợi" : item.impact === "BENEFICIAL" ? "Có lợi" : item.impact === "BALANCED" ? "Cân bằng" : "Chung"}</span><span className={styles.findingType}>{findingTypeLabels[item.finding_type]}</span><span className={`${styles.riskBadge} ${riskClass(item.severity)}`}>{item.severity}</span></div>
            <div className={styles.findingExplanation}><p><b>Lý do:</b> {item.reason}</p><p><b>Khuyến nghị:</b> {item.recommendation}</p></div>
            <div className={styles.redlineGrid}>
              <div><small>ORIGINAL</small><blockquote>{item.original_text}</blockquote></div>
              <div><small>AI RECOMMENDATION</small>{editingId === item.id ? <textarea value={draft} onChange={(event) => setDrafts((current) => ({ ...current, [item.finding_key]: event.target.value }))} /> : <blockquote>{draft}</blockquote>}</div>
            </div>
            {item.sources.length > 0 && <div className={styles.findingSources}>{item.sources.map((source) => source.url ? <a key={source.id} href={source.url} target="_blank" rel="noreferrer">{source.title}</a> : <span key={source.id}>{source.title}</span>)}</div>}
            <div className={styles.findingActions}>
              <button disabled={saving === item.finding_key} className={decision === "ACCEPTED" ? styles.accepted : ""} onClick={() => setDecision(item.finding_key, "ACCEPTED")}><Check size={13} />Accept</button>
              <button disabled={saving === item.finding_key} className={decision === "REJECTED" ? styles.rejected : ""} onClick={() => setDecision(item.finding_key, "REJECTED")}><X size={13} />Reject</button>
              <button disabled={saving === item.finding_key} className={decision === "EDITED" ? styles.edited : ""} onClick={() => {
                if (editingId === item.id) { setEditingId(null); void saveDecision(item.finding_key, "EDITED", draft); }
                else { setDrafts((current) => ({ ...current, [item.finding_key]: draft })); setEditingId(item.id); }
              }}>{editingId === item.id ? <><Check size={13} />Lưu bản sửa</> : "Edit"}</button>
              {saving === item.finding_key
                ? <small>Đang lưu…</small>
                : decision && <small>{decision === "ACCEPTED" ? "Đã chấp nhận đề xuất" : decision === "REJECTED" ? "Đã từ chối" : "Đã lưu bản chỉnh sửa"}</small>}
            </div>
          </article>;
        })}</div>
      </section>

      {review.reference_sources.length > 0 && <section className={styles.reviewSection}><div className={styles.reviewSectionTitle}><div><strong>Nguồn tham chiếu</strong><small>Click vào tên nguồn để mở và đọc nội dung</small></div></div><div className={styles.referenceList}>{review.reference_sources.map((source) => <div key={`${source.id}-${source.type}`}><span>{source.type}</span><button type="button" onClick={() => void openReference(source)} disabled={readerLoading}><span>{source.title}</span>{source.url ? <ExternalLink size={12} /> : <Eye size={12} />}</button><small>{source.note}</small></div>)}</div></section>}
      <p className={styles.reviewDisclaimer}>{review.review_disclaimer}</p>
    </div>
    {(reader || readerLoading || readerError) && <div className={styles.readerBackdrop} role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) { setReader(null); setReaderError(null); } }}>
      <section className={styles.readerModal} role="dialog" aria-modal="true" aria-label="Trình đọc tài liệu tham chiếu">
        <header><div><span><FileText size={19} /></span><div><strong>{reader?.document_title || "Đang mở tài liệu…"}</strong><small>{reader ? `${reader.document_name}${reader.version ? ` · v${reader.version}` : ""}` : "Đang tải nội dung theo quyền truy cập"}</small></div></div><button type="button" onClick={() => { setReader(null); setReaderError(null); }} aria-label="Đóng trình đọc"><X size={17} /></button></header>
        {readerLoading ? <div className={styles.readerState}><Loader2 className={styles.spin} size={20} />Đang đọc tài liệu…</div> : readerError ? <div className={`${styles.readerState} ${styles.readerError}`}><AlertTriangle size={18} />{readerError}</div> : reader && <>
          <div className={styles.readerMeta}><span>{reader.document_type || "DOCUMENT"}</span>{reader.chunk_count != null && <span>{reader.chunk_count} chunks</span>}{reader.character_count != null && <span>{reader.character_count.toLocaleString("vi-VN")} ký tự</span>}</div>
          <pre className={styles.readerContent}>{reader.content}</pre>
          <footer>{reader.source_url && <a href={reader.source_url} target="_blank" rel="noreferrer"><ExternalLink size={13} />Mở trang gốc</a>}{reader.download_url && <button type="button" onClick={() => void downloadOriginal()}><Download size={13} />Tải file gốc</button>}<button type="button" onClick={() => { setReader(null); setReaderError(null); }}>Đóng</button></footer>
        </>}
      </section>
    </div>}
  </div>;
}
