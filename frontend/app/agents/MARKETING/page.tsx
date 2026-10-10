"use client";

import {
  AlertTriangle,
  ArrowLeft,
  Bot,
  ChevronRight,
  FolderOpen,
  Loader2,
  Megaphone,
  MessageSquareText,
  Send,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";
import { KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import ChatMessageContent from "@/components/chat/ChatMessageContent";
import { DraftsReview, OutlineReview, StageBadge, StagePipeline, type StageState } from "@/components/marketing/CampaignReview";
import { type Campaign, DRAFT_STAGES, OUTLINE_STAGES, type Platform } from "@/components/marketing/campaign";
import styles from "@/components/marketing/marketing.module.css";
import api from "@/lib/api";
import { type ExecutionPhase, streamAgentChat } from "@/lib/chatStream";
import { streamMarketing } from "@/lib/marketingStream";
import { useAuthStore, userCan } from "@/store/useAuthStore";

type View = "new" | "campaigns" | "chat";

interface Agent { name: string; is_active: boolean }
interface ToolCall { tool_name?: string; status?: string }
interface Citation { document_title?: string; document_name?: string; section_title?: string | null }
interface Message { id: string; sender: "USER" | "ASSISTANT"; content: string; tools_executed?: ToolCall[]; citations?: Citation[] }
interface Conversation { id: string; agent_role: string }
interface Running { kind: "outline" | "drafts"; state: Record<string, StageState> }

const PHASES: Record<ExecutionPhase, string> = {
  ANALYZING: "Đang đọc yêu cầu",
  SEARCHING: "Đang tìm tài liệu",
  TOOL_CALLING: "Đang lập dàn ý chiến dịch",
  WAITING_APPROVAL: "Đang chờ phê duyệt",
  COMPLETED: "Xong",
};

const BRIEF_HINT =
  "Sản phẩm/dịch vụ, mục tiêu (con số, thời hạn), đối tượng khách hàng, thông điệp hoặc ưu đãi muốn nhấn mạnh…";
const EXAMPLE_BRIEF =
  "Ra mắt ứng dụng PayNow giúp kế toán doanh nghiệp SME đối soát hoá đơn tự động. Mục tiêu 500 lead đăng ký dùng thử trong tháng 11. Đối tượng: kế toán trưởng và chủ doanh nghiệp nhỏ.";

// "[Citation: <document>, v1.0, <section>; chunk=<id>]": the answer is shown without them,
// its sources as labels underneath.
const CITATION_TAG = /\s*\[Citation:[^\]]*\]/gi;

function sourceLabels(citations: Citation[] | undefined): string[] {
  return Array.from(new Set((citations ?? []).map((item) => {
    const title = item.document_title || item.document_name || "Tài liệu";
    return item.section_title ? `${title} — ${item.section_title}` : title;
  })));
}

function errorText(reason: unknown): string {
  const detail = (reason as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  return reason instanceof Error ? reason.message : "Đã có lỗi, vui lòng thử lại.";
}

/**
 * The Marketing agent's workspace. A campaign runs in two steps, each stopping for its
 * author: brief → outline (approve, edit or reject), then the Facebook, Instagram and
 * Threads posts with their fact-check (edit and settle) → send for approval.
 */
export default function MarketingAgentPage() {
  const router = useRouter();
  const { user, isAuthenticated, hasHydrated } = useAuthStore();
  const canCreate = userCan(user, "marketing.campaign.create");
  const [view, setView] = useState<View>("new");
  const [agent, setAgent] = useState<Agent | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [brief, setBrief] = useState("");
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [selected, setSelected] = useState<Campaign | null>(null);
  const [running, setRunning] = useState<Running | null>(null);
  const [busy, setBusy] = useState(false);

  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState("");
  const [phase, setPhase] = useState<ExecutionPhase | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const loadCampaigns = useCallback(async () => {
    try {
      const { data } = await api.get<Campaign[]>("/api/v1/marketing/campaigns");
      setCampaigns(data);
    } catch (reason) {
      setError(errorText(reason));
    }
  }, []);

  const openCampaign = useCallback(async (id: string) => {
    setError(null);
    try {
      const { data } = await api.get<Campaign>(`/api/v1/marketing/campaigns/${id}`);
      setSelected(data);
      setView("campaigns");
    } catch (reason) {
      setError(errorText(reason));
    }
  }, []);

  useEffect(() => {
    if (!hasHydrated) return;
    if (!isAuthenticated) {
      router.replace("/login");
      return;
    }
    const timer = window.setTimeout(() => {
      // Someone who may only read campaigns lands on the list, not on an empty brief form.
      if (!userCan(useAuthStore.getState().user, "marketing.campaign.create")) setView("campaigns");
      void api.get<Agent>("/api/v1/agents/MARKETING").then(({ data }) => setAgent(data)).catch(() => setAgent(null));
      void loadCampaigns();
      // A link from the chat ("Mở chiến dịch") opens that campaign.
      const fromLink = new URLSearchParams(window.location.search).get("campaign");
      if (fromLink) void openCampaign(fromLink);
      void api.get<Conversation[]>("/api/v1/agent/conversations").then(async ({ data }) => {
        const latest = data.find((item) => item.agent_role === "MARKETING");
        if (!latest) return;
        const detail = await api.get<{ messages: Message[] }>(`/api/v1/agent/conversations/${latest.id}`);
        setConversationId(latest.id);
        setMessages(detail.data.messages);
      }).catch(() => undefined);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [hasHydrated, isAuthenticated, loadCampaigns, openCampaign, router]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, phase, view]);

  /** Run one streamed step; the campaign it returns becomes the one on screen. */
  const runStep = async (kind: Running["kind"], path: string, body: Record<string, unknown>) => {
    setError(null);
    setNotice(null);
    setBusy(true);
    setRunning({ kind, state: {} });
    try {
      let result: Campaign | null = null;
      let failure: string | null = null;
      await streamMarketing<Campaign>(path, body, (event) => {
        if (event.event === "progress") {
          setRunning((current) => current && {
            ...current,
            state: { ...current.state, [event.data.stage]: { status: event.data.status, detail: event.data.detail } },
          });
        } else if (event.event === "complete") {
          result = event.data;
        } else if (event.event === "error") {
          failure = event.data.message;
        }
      });
      if (failure) throw new Error(failure);
      if (!result) throw new Error("Phản hồi chưa hoàn chỉnh, vui lòng thử lại.");
      setSelected(result);
      setView("campaigns");
      void loadCampaigns();
      return true;
    } catch (reason) {
      setError(errorText(reason));
      if (selected) void openCampaign(selected.id);
      return false;
    } finally {
      setBusy(false);
      setRunning(null);
    }
  };

  const startCampaign = async () => {
    if (!brief.trim() || busy) return;
    if (await runStep("outline", "/api/v1/marketing/campaigns/stream", { brief: brief.trim() })) setBrief("");
  };

  const decideOutline = (body: Record<string, unknown>) => {
    if (!selected) return;
    const kind = body.action === "reject" ? "outline" : "drafts";
    void runStep(kind, `/api/v1/marketing/campaigns/${selected.id}/outline/stream`, body);
  };

  const settleDrafts = async (updated: Partial<Record<Platform, string>> | null) => {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      const { data } = await api.post<Campaign>(
        `/api/v1/marketing/campaigns/${selected.id}/drafts`,
        updated ? { action: "edit", updated_drafts: updated } : { action: "approve" },
      );
      setSelected({ ...data, sources: selected.sources, skills: selected.skills });
      void loadCampaigns();
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy(false);
    }
  };

  const submit = async () => {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      const { data } = await api.post<{ campaign: Campaign; warning: string | null }>(
        `/api/v1/marketing/campaigns/${selected.id}/submit-approval`,
      );
      setSelected({ ...data.campaign, sources: selected.sources, skills: selected.skills });
      setNotice(data.warning ?? "Đã gửi phê duyệt. Người có quyền duyệt nội dung sẽ nhận thông báo.");
      void loadCampaigns();
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy(false);
    }
  };

  const ask = async (text: string) => {
    const content = text.trim();
    if (!content || busy) return;
    setView("chat");
    setQuestion("");
    setError(null);
    setBusy(true);
    setPhase("ANALYZING");
    setMessages((current) => [...current, { id: `pending-${Date.now()}`, sender: "USER", content }]);
    try {
      let finished: { conversation_id?: string } | null = null;
      await streamAgentChat({ agent_role: "MARKETING", message: content, conversation_id: conversationId }, (event) => {
        if (event.event === "status") setPhase(event.phase);
        else if (event.event === "complete") finished = event as { conversation_id?: string };
        else if (event.event === "error") throw new Error(event.message);
      });
      const id = (finished as { conversation_id?: string } | null)?.conversation_id;
      if (!id) throw new Error("Phản hồi chưa hoàn chỉnh, vui lòng thử lại.");
      const detail = await api.get<{ messages: Message[] }>(`/api/v1/agent/conversations/${id}`);
      setConversationId(id);
      setMessages(detail.data.messages);
      void loadCampaigns();
    } catch (reason) {
      setMessages((current) => current.filter((item) => !item.id.startsWith("pending-")));
      setQuestion(content);
      setError(errorText(reason));
    } finally {
      setBusy(false);
      setPhase(null);
      inputRef.current?.focus();
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void ask(question);
    }
  };

  if (!hasHydrated || !isAuthenticated) return null;

  const mine = selected && user ? selected.created_by_id === user.id : false;
  const views: { key: View; label: string; icon: typeof Megaphone }[] = [
    ...(canCreate ? [{ key: "new" as View, label: "Chiến dịch mới", icon: Sparkles }] : []),
    { key: "campaigns", label: "Chiến dịch", icon: FolderOpen },
    { key: "chat", label: "Chat với trợ lý", icon: MessageSquareText },
  ];

  return (
    <div className={styles.page}>
      <Sidebar agentStatuses={{ MARKETING: agent?.is_active !== false }} />
      <div className={styles.shell}>
        <header className={styles.topbar}>
          <div className={styles.breadcrumb}><span>Nhân viên AI</span><ChevronRight size={14} /><strong>Trợ lý Marketing</strong></div>
          <div className={styles.topActions}>
            <button type="button" className={styles.iconButton} title="Trung tâm phê duyệt" onClick={() => router.push("/approvals")}><ShieldCheck size={17} /></button>
            <span className={styles.status}><span />{agent?.is_active === false ? "Tạm dừng" : "Đang hoạt động"}</span>
          </div>
        </header>

        <div className={styles.workspace}>
          <aside className={styles.rail}>
            <div className={styles.agentIdentity}>
              <div className={styles.agentMark}><Megaphone size={21} /></div>
              <div><strong>{agent?.name || "Trợ lý Marketing"}</strong><span>Chiến dịch đa kênh</span></div>
            </div>
            <nav className={styles.nav} aria-label="Không gian Marketing">
              {views.map(({ key, label, icon: Icon }) => (
                <button key={key} className={view === key ? styles.active : ""} onClick={() => { setView(key); if (key === "campaigns") setSelected(null); }} aria-current={view === key ? "page" : undefined}>
                  <Icon size={17} />{label}
                </button>
              ))}
            </nav>
            <div className={styles.railNote}>
              <strong><ShieldCheck size={14} />Bạn duyệt từng bước</strong>
              Trợ lý dừng sau dàn ý và sau bài viết để bạn duyệt; số liệu được kiểm chứng với tài liệu công ty. Không có gì được tự đăng.
            </div>
          </aside>

          <main className={styles.main}>
            {error && <div className={styles.error}><AlertTriangle size={17} /><span>{error}</span><button onClick={() => setError(null)} title="Đóng"><X size={15} /></button></div>}
            {notice && <div className={styles.notice}><ShieldCheck size={15} /><span style={{ flex: 1 }}>{notice}</span><button className={styles.linkButton} onClick={() => setNotice(null)}><X size={13} /></button></div>}

            {running && (
              <section className={styles.panel} style={{ marginBottom: 14 }}>
                <div className={styles.panelHeader}><div><h2>{running.kind === "outline" ? "Đang soạn dàn ý" : "Đang viết và kiểm chứng bài"}</h2><p>Có thể mất một đến vài phút.</p></div></div>
                <StagePipeline stages={running.kind === "outline" ? OUTLINE_STAGES : DRAFT_STAGES} state={running.state} />
              </section>
            )}

            {view === "new" && canCreate && !running && (
              <>
                <div className={styles.headingRow}>
                  <div><span className={styles.eyebrow}>MARKETING CAMPAIGN</span><h1>Lập chiến dịch truyền thông</h1><p>Nhập brief; trợ lý tra tài liệu công ty, soạn dàn ý để bạn duyệt rồi mới viết bài.</p></div>
                </div>
                <section className={styles.panel}>
                  <div className={styles.panelBody}>
                    <textarea
                      className={styles.textarea}
                      style={{ minHeight: 170 }}
                      value={brief}
                      maxLength={8000}
                      onChange={(event) => setBrief(event.target.value)}
                      placeholder={BRIEF_HINT}
                      aria-label="Brief chiến dịch"
                    />
                  </div>
                  <div className={styles.actions}>
                    <button className={`${styles.linkButton} ${styles.grow}`} style={{ textAlign: "left" }} onClick={() => setBrief(EXAMPLE_BRIEF)}>Dùng brief mẫu</button>
                    <button className={styles.primaryButton} disabled={busy || !brief.trim()} onClick={() => void startCampaign()}>
                      {busy ? <Loader2 size={15} className={styles.spin} /> : <Sparkles size={15} />}Soạn dàn ý
                    </button>
                  </div>
                </section>
              </>
            )}

            {view === "campaigns" && !running && !selected && (
              <section className={styles.panel}>
                <div className={styles.panelHeader}><div><h2>Chiến dịch</h2><p>Mới nhất trước</p></div></div>
                {campaigns.length === 0
                  ? <div className={styles.empty}><FolderOpen size={22} />Chưa có chiến dịch nào.</div>
                  : (
                    <div className={styles.campaignList}>
                      {campaigns.map((item) => (
                        <button key={item.id} onClick={() => void openCampaign(item.id)}>
                          <span>
                            <strong>{item.title}</strong>
                            <small>{item.created_by_name ? `${item.created_by_name} · ` : ""}{item.updated_at ? new Date(item.updated_at).toLocaleString("vi-VN") : ""}</small>
                          </span>
                          <StageBadge stage={item.stage} />
                          <ChevronRight size={15} />
                        </button>
                      ))}
                    </div>
                  )}
              </section>
            )}

            {view === "campaigns" && !running && selected && (
              <>
                <div className={styles.headingRow}>
                  <div>
                    <button className={styles.linkButton} onClick={() => setSelected(null)}><ArrowLeft size={12} /> Tất cả chiến dịch</button>
                    <h1>{selected.title}</h1>
                    <p>{selected.created_by_name ? `Lập bởi ${selected.created_by_name}` : ""}</p>
                  </div>
                </div>
                <details className={styles.source} style={{ marginBottom: 14 }}>
                  <summary><b>B</b><span>Brief</span></summary>
                  <p>{selected.brief}</p>
                </details>
                {selected.stage === "OUTLINE_PENDING" && mine && (
                  <OutlineReview
                    key={`${selected.id}-${selected.updated_at}`}
                    campaign={selected}
                    busy={busy}
                    onApprove={() => decideOutline({ action: "approve" })}
                    onEdit={(outline) => decideOutline({ action: "edit", updated_outline: outline })}
                    onReject={(feedback) => decideOutline({ action: "reject", feedback })}
                  />
                )}
                {selected.stage === "DRAFTING" && <div className={styles.empty}><Loader2 size={20} className={styles.spin} />Bài viết đang được soạn; tải lại sau ít phút.</div>}
                {selected.stage === "OUTLINE_PENDING" && !mine && (
                  <section className={styles.panel}><div className={styles.panelBody}><div className={styles.outline}><ChatMessageContent content={selected.outline ?? ""} /></div></div></section>
                )}
                {["DRAFTS_PENDING", "FINAL", "SUBMITTED", "APPROVED", "REJECTED"].includes(selected.stage) && (
                  <DraftsReview
                    key={`${selected.id}-${selected.updated_at}`}
                    campaign={mine ? selected : { ...selected, stage: selected.stage === "DRAFTS_PENDING" || selected.stage === "REJECTED" || selected.stage === "FINAL" ? "SUBMITTED" : selected.stage }}
                    busy={busy}
                    onSettle={(updated) => void settleDrafts(updated)}
                    onSubmit={() => void submit()}
                  />
                )}
                {selected.stage !== "OUTLINE_PENDING" && selected.outline && (
                  <details className={styles.source} style={{ marginTop: 14 }}>
                    <summary><b>D</b><span>Dàn ý đã duyệt</span></summary>
                    <div className={styles.panelBody}><div className={styles.outline}><ChatMessageContent content={selected.outline} /></div></div>
                  </details>
                )}
              </>
            )}

            {view === "chat" && (
              <section className={styles.chatWorkspace}>
                <header className={styles.chatHeader}>
                  <div>
                    <span className={`${styles.statIcon} ${styles.amber}`}><Bot size={19} /></span>
                    <div><strong>Chat với Trợ lý Marketing</strong><small>Hỏi kiến thức marketing, hoặc gửi brief để lập chiến dịch</small></div>
                  </div>
                  <button type="button" onClick={() => { setMessages([]); setConversationId(null); setQuestion(""); inputRef.current?.focus(); }}>
                    <MessageSquareText size={14} />Cuộc trò chuyện mới
                  </button>
                </header>
                <div className={styles.chatMessages}>
                  {messages.length === 0 && !busy && (
                    <div className={styles.chatEmpty}>
                      <span><Megaphone size={28} /></span>
                      <h2>Bạn cần hỗ trợ marketing gì?</h2>
                      <p>Hỏi về kênh, thông điệp, cách đo hiệu quả… hoặc gửi brief kèm “lập chiến dịch” để trợ lý soạn dàn ý.</p>
                    </div>
                  )}
                  {messages.map((message) => (
                    <article key={message.id} className={`${styles.message} ${message.sender === "USER" ? styles.userMessage : ""}`}>
                      <span>{message.sender === "USER" ? "Bạn" : <Bot size={15} />}</span>
                      <div className={styles.bubble}>
                        {message.sender === "USER" ? <div>{message.content}</div> : <ChatMessageContent content={message.content.replace(CITATION_TAG, "")} />}
                        {message.sender === "ASSISTANT" && sourceLabels(message.citations).length > 0 && (
                          <div className={styles.toolTrail} aria-label="Nguồn">
                            {sourceLabels(message.citations).map((label) => <span key={label}>{label}</span>)}
                          </div>
                        )}
                      </div>
                    </article>
                  ))}
                  {busy && view === "chat" && (
                    <article className={styles.message}>
                      <span><Bot size={15} /></span>
                      <div className={styles.bubble}><div className={styles.phase}><Loader2 size={14} className={styles.spin} />{phase ? PHASES[phase] : "Đang xử lý"}…</div></div>
                    </article>
                  )}
                  <div ref={endRef} />
                </div>
                <div>
                  <form className={styles.composer} onSubmit={(event) => { event.preventDefault(); void ask(question); }}>
                    <textarea
                      ref={inputRef}
                      rows={1}
                      value={question}
                      onChange={(event) => setQuestion(event.target.value)}
                      onKeyDown={onKeyDown}
                      placeholder="Ví dụ: Lập chiến dịch ra mắt sản phẩm X cho khách hàng SME tháng 11…"
                      aria-label="Câu hỏi cho Trợ lý Marketing"
                    />
                    <button type="submit" disabled={busy || !question.trim()} aria-label="Gửi">
                      {busy ? <Loader2 size={17} className={styles.spin} /> : <Send size={17} />}
                    </button>
                  </form>
                  <div className={styles.composerHint}>Enter để gửi · Shift + Enter để xuống dòng</div>
                </div>
              </section>
            )}
          </main>
        </div>
      </div>
    </div>
  );
}
