"use client";

import {
  AlertTriangle,
  ArrowRight,
  BookOpenCheck,
  Bot,
  CalendarClock,
  ChevronRight,
  CircleGauge,
  Database,
  FileSpreadsheet,
  FileWarning,
  HandCoins,
  Landmark,
  Loader2,
  MessageSquareText,
  Receipt,
  Send,
  ShieldCheck,
  Sparkles,
  Wallet,
  X,
} from "lucide-react";
import { KeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import ChatMessageContent from "@/components/chat/ChatMessageContent";
import BooksTab from "@/components/finance/BooksTab";
import DataTab from "@/components/finance/DataTab";
import DebtsTab from "@/components/finance/DebtsTab";
import FinanceChart, { type ChartSpec } from "@/components/finance/FinanceChart";
import InvoicesTab from "@/components/finance/InvoicesTab";
import SheetsTab from "@/components/finance/SheetsTab";
import { apiError, currentPeriod, formatVnd, shiftPeriod } from "@/components/finance/format";
import api from "@/lib/api";
import { type ExecutionPhase, streamAgentChat } from "@/lib/chatStream";
import { useAuthStore, userCan } from "@/store/useAuthStore";
import styles from "./finance.module.css";

type View = "overview" | "chat" | "invoices" | "books" | "debts" | "sheets" | "data";

interface Agent { name: string; is_active: boolean }
interface Attachment { type: string; payload?: Record<string, unknown> }
interface ToolCall { tool_name?: string; status?: string }
interface Message {
  id: string;
  sender: "USER" | "ASSISTANT";
  content: string;
  attachments?: Attachment[];
  tools_executed?: ToolCall[];
}
interface Conversation { id: string; agent_role: string; updated_at?: string }
interface ScheduleRow { invoice_id: string; party: string; series: string; number: string; due_date: string; overdue: boolean; remaining: string }
interface Schedule { invoices: ScheduleRow[] }
interface Aging { total: string; buckets: Record<string, string>; parties: unknown[] }
interface Charted { charts: ChartSpec[]; rows?: unknown[] }
interface Approval { id: string; action_type: string }

const PHASES: Record<ExecutionPhase, string> = {
  ANALYZING: "Đang phân tích câu hỏi",
  SEARCHING: "Đang tìm tài liệu",
  TOOL_CALLING: "Đang đọc sổ sách",
  WAITING_APPROVAL: "Đang chờ phê duyệt",
  COMPLETED: "Xong",
};

// What each Finance tool did, in the reader's words: shown under an answer so they see
// which books it came from.
const TOOL_LABELS: Record<string, string> = {
  lookup_invoices: "Tra hoá đơn",
  propose_journal_entry: "Lập bút toán nháp",
  get_account_balance: "Số dư tài khoản",
  get_account_trend: "Xu hướng tài khoản",
  get_trial_balance: "Bảng cân đối phát sinh",
  get_ledger_detail: "Sổ chi tiết",
  get_expense_breakdown: "Cơ cấu chi phí",
  budget_vs_actual: "Ngân sách",
  ar_ap_aging: "Tuổi nợ",
  payment_schedule: "Lịch trả tiền",
  draft_payment_voucher: "Lập phiếu chi nháp",
  draft_payment_reminder: "Soạn thư nhắc nợ",
  list_spreadsheets: "File Excel của bạn",
  analyze_spreadsheet: "Phân tích file Excel",
  rag_search: "Tra văn bản",
};
const DRAFTING = new Set(["propose_journal_entry", "draft_payment_voucher", "draft_payment_reminder"]);
const SHEET_TOOLS = new Set(["list_spreadsheets", "analyze_spreadsheet"]);

const SUGGESTIONS = [
  "Tháng này phòng nào chi vượt ngân sách?",
  "Khách hàng nào đang nợ quá hạn?",
  "30 ngày tới phải trả những hoá đơn nào?",
  "Cơ cấu chi phí tháng trước theo phòng ban",
  "Số dư tiền gửi ngân hàng 6 tháng gần đây",
];

/**
 * The Finance agent's workspace, laid out like the Legal agent's: an overview of what
 * needs doing, the chat, and the finance screens (invoices, books, debts, spreadsheets)
 * one click away. Every figure on the overview comes from the same report endpoints the
 * /finance page reads; each block shows only to positions holding its box.
 */
export default function FinanceAgentPage() {
  const router = useRouter();
  const { user, isAuthenticated, hasHydrated } = useAuthStore();
  const can = useCallback((code: string) => userCan(user, code), [user]);
  const [view, setView] = useState<View>("overview");
  const [agent, setAgent] = useState<Agent | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Overview figures.
  const [exceptions, setExceptions] = useState<number | null>(null);
  const [pendingApprovals, setPendingApprovals] = useState<number | null>(null);
  const [aging, setAging] = useState<Aging | null>(null);
  const [schedule, setSchedule] = useState<Schedule | null>(null);
  const [expenses, setExpenses] = useState<{ period: string; data: Charted } | null>(null);

  // Chat.
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState<ExecutionPhase | null>(null);
  const [streamed, setStreamed] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const views = useMemo(() => {
    const list: { key: View; label: string; icon: typeof Receipt }[] = [
      { key: "overview", label: "Tổng quan", icon: CircleGauge },
      { key: "chat", label: "Chat với trợ lý", icon: MessageSquareText },
    ];
    if (can("finance.invoice.process") || can("finance.ledger.view") || can("finance.ar_ap.view")) {
      list.push({ key: "invoices", label: "Hoá đơn", icon: Receipt });
    }
    if (can("finance.ledger.view") || can("finance.budget.view_own") || can("finance.journal.draft")) {
      list.push({ key: "books", label: "Sổ sách & biểu đồ", icon: BookOpenCheck });
    }
    if (can("finance.ar_ap.view")) list.push({ key: "debts", label: "Công nợ & phiếu chi", icon: HandCoins });
    if (can("finance.sheet.analyze")) list.push({ key: "sheets", label: "Phân tích Excel", icon: FileSpreadsheet });
    if (can("finance.import.manage")) list.push({ key: "data", label: "Dữ liệu & cài đặt", icon: Database });
    return list;
  }, [can]);
  const allowed = (key: View) => views.some((item) => item.key === key);

  const loadOverview = useCallback(async () => {
    const quietly = <T,>(promise: Promise<{ data: T }>) => promise.then(({ data }) => data).catch(() => null);
    const period = currentPeriod();
    const [agentData, invoices, approvals, agingData, scheduleData, thisMonth] = await Promise.all([
      quietly(api.get<Agent>("/api/v1/agents/FINANCE")),
      can("finance.invoice.process") || can("finance.ledger.view") || can("finance.ar_ap.view")
        ? quietly(api.get<unknown[]>("/api/v1/finance/invoices", { params: { status: "EXCEPTION" } }))
        : Promise.resolve(null),
      quietly(api.get<Approval[]>("/api/v1/approvals/pending")),
      can("finance.ar_ap.view") ? quietly(api.get<Aging>("/api/v1/finance/reports/aging", { params: { kind: "RECEIVABLE" } })) : Promise.resolve(null),
      can("finance.ar_ap.view") ? quietly(api.get<Schedule>("/api/v1/finance/reports/payment-schedule", { params: { horizon_days: 30 } })) : Promise.resolve(null),
      can("finance.ledger.view")
        ? quietly(api.get<Charted>("/api/v1/finance/reports/expenses", { params: { from_period: period, to_period: period } }))
        : Promise.resolve(null),
    ]);
    setAgent(agentData);
    setExceptions(invoices ? invoices.length : null);
    setPendingApprovals(approvals ? approvals.filter((item) => item.action_type.startsWith("FINANCE_")).length : null);
    setAging(agingData);
    setSchedule(scheduleData);
    if (thisMonth && thisMonth.charts.length) {
      setExpenses({ period, data: thisMonth });
    } else if (can("finance.ledger.view")) {
      // Early in a month the books are often still empty: show the month before.
      const previous = shiftPeriod(period, -1);
      const lastMonth = await quietly(api.get<Charted>("/api/v1/finance/reports/expenses", { params: { from_period: previous, to_period: previous } }));
      setExpenses(lastMonth ? { period: previous, data: lastMonth } : null);
    }
  }, [can]);

  const loadLatestConversation = useCallback(async () => {
    try {
      const { data } = await api.get<Conversation[]>("/api/v1/agent/conversations");
      const latest = data.find((item) => item.agent_role === "FINANCE");
      if (!latest) return;
      const detail = await api.get<{ messages: Message[] }>(`/api/v1/agent/conversations/${latest.id}`);
      setConversationId(latest.id);
      setMessages(detail.data.messages);
    } catch (reason) {
      setError(apiError(reason).message);
    }
  }, []);

  useEffect(() => {
    if (!hasHydrated) return;
    if (!isAuthenticated) {
      router.replace("/login");
      return;
    }
    const timer = window.setTimeout(() => {
      void loadOverview();
      void loadLatestConversation();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [hasHydrated, isAuthenticated, loadLatestConversation, loadOverview, router]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, streamed, phase, view]);

  const ask = async (text: string) => {
    const content = text.trim();
    if (!content || busy) return;
    setView("chat");
    setQuestion("");
    setError(null);
    setBusy(true);
    setPhase("ANALYZING");
    setStreamed("");
    setMessages((current) => [...current, { id: `pending-${Date.now()}`, sender: "USER", content }]);
    try {
      let finished: { conversation_id?: string } | null = null;
      await streamAgentChat({ agent_role: "FINANCE", message: content, conversation_id: conversationId }, (event) => {
        if (event.event === "status") setPhase(event.phase);
        else if (event.event === "token") setStreamed((current) => current + event.delta);
        else if (event.event === "complete") finished = event as { conversation_id?: string };
        else if (event.event === "error") throw new Error(event.message);
      });
      const id = (finished as { conversation_id?: string } | null)?.conversation_id;
      if (!id) throw new Error("Phản hồi chưa hoàn chỉnh, vui lòng thử lại.");
      // The stored messages carry the charts and tool trail; read them back rather than
      // rebuilding them from the stream.
      const detail = await api.get<{ messages: Message[] }>(`/api/v1/agent/conversations/${id}`);
      setConversationId(id);
      setMessages(detail.data.messages);
      void loadOverview();
    } catch (reason) {
      setMessages((current) => current.filter((item) => !item.id.startsWith("pending-")));
      setQuestion(content);
      setError(reason instanceof Error ? reason.message : apiError(reason).message);
    } finally {
      setBusy(false);
      setPhase(null);
      setStreamed("");
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

  const overdue = aging
    ? Object.entries(aging.buckets).filter(([key]) => key !== "NOT_DUE").reduce((sum, [, value]) => sum + Number(value), 0)
    : null;
  const dueRows = (schedule?.invoices ?? []).filter((row) => Number(row.remaining) > 0);
  const dueTotal = dueRows.reduce((sum, row) => sum + Number(row.remaining), 0);

  return (
    <div className={styles.page}>
      <Sidebar agentStatuses={{ FINANCE: agent?.is_active !== false }} />
      <div className={styles.shell}>
        <header className={styles.topbar}>
          <div className={styles.breadcrumb}><span>Nhân viên AI</span><ChevronRight size={14} /><strong>Trợ lý Tài chính</strong></div>
          <div className={styles.topActions}>
            <button type="button" className={styles.iconButton} title="Trung tâm phê duyệt" onClick={() => router.push("/approvals")}><ShieldCheck size={17} /></button>
            <span className={styles.status}><span />{agent?.is_active === false ? "Tạm dừng" : "Đang hoạt động"}</span>
          </div>
        </header>

        <div className={styles.workspace}>
          <aside className={styles.rail}>
            <div className={styles.agentIdentity}>
              <div className={styles.agentMark}><Landmark size={21} /></div>
              <div><strong>{agent?.name || "Trợ lý Tài chính"}</strong><span>Tài chính – Kế toán</span></div>
            </div>
            <nav className={styles.nav} aria-label="Không gian Tài chính">
              {views.map(({ key, label, icon: Icon }) => (
                <button key={key} className={view === key ? styles.active : ""} onClick={() => setView(key)} aria-current={view === key ? "page" : undefined}>
                  <Icon size={17} />{label}
                  {key === "invoices" && !!exceptions && <span className={styles.navBadge}>{exceptions}</span>}
                </button>
              ))}
            </nav>
            <div className={styles.railNote}>
              <strong><ShieldCheck size={14} />Mọi bút toán đều là nháp</strong>
              Trợ lý chỉ đọc sổ sách và soạn bản nháp; ghi sổ, chi tiền hay gửi thư chỉ diễn ra khi người có quyền duyệt.
            </div>
          </aside>

          <main className={styles.main}>
            {error && <div className={styles.error}><AlertTriangle size={17} /><span>{error}</span><button onClick={() => setError(null)} title="Đóng"><X size={15} /></button></div>}

            {view === "overview" && (
              <>
                <div className={styles.headingRow}>
                  <div><span className={styles.eyebrow}>FINANCE OPERATIONS</span><h1>Trung tâm tài chính</h1><p>Việc cần xử lý hôm nay, lấy trực tiếp từ sổ sách của công ty.</p></div>
                  <button className={styles.primaryButton} onClick={() => { setView("chat"); window.setTimeout(() => inputRef.current?.focus(), 0); }}><Sparkles size={16} />Hỏi trợ lý</button>
                </div>

                <section className={styles.stats}>
                  <button className={styles.stat} onClick={() => allowed("invoices") && setView("invoices")} disabled={!allowed("invoices")}>
                    <span className={`${styles.statIcon} ${styles.amber}`}><FileWarning size={19} /></span>
                    <div><strong>{exceptions ?? "—"}</strong><small>Hoá đơn cần xem lại</small></div><ArrowRight size={15} />
                  </button>
                  <button className={styles.stat} onClick={() => router.push("/approvals")}>
                    <span className={`${styles.statIcon} ${styles.violet}`}><ShieldCheck size={19} /></span>
                    <div><strong>{pendingApprovals ?? "—"}</strong><small>Phiếu tài chính chờ bạn duyệt</small></div><ArrowRight size={15} />
                  </button>
                  <button className={styles.stat} onClick={() => allowed("debts") && setView("debts")} disabled={!allowed("debts")}>
                    <span className={`${styles.statIcon} ${styles.red}`}><HandCoins size={19} /></span>
                    <div><strong>{overdue === null ? "—" : formatVnd(overdue)}</strong><small>Khách hàng nợ quá hạn</small></div><ArrowRight size={15} />
                  </button>
                  <button className={styles.stat} onClick={() => allowed("debts") && setView("debts")} disabled={!allowed("debts")}>
                    <span className={`${styles.statIcon} ${styles.blue}`}><Wallet size={19} /></span>
                    <div><strong>{schedule ? formatVnd(dueTotal) : "—"}</strong><small>Phải trả trong 30 ngày</small></div><ArrowRight size={15} />
                  </button>
                </section>

                <div className={styles.dashboardGrid}>
                  <div style={{ display: "grid", gap: 14, minWidth: 0 }}>
                    <section className={styles.panel}>
                      <div className={styles.panelHeader}><div><h2>Công cụ tài chính</h2><p>Mở nghiệp vụ, hoặc nhờ trợ lý làm giúp</p></div></div>
                      <div className={styles.toolGrid}>
                        <Tool icon={MessageSquareText} tone="green" title="Hỏi đáp số liệu" meta="Số dư · Ngân sách · Công nợ" onClick={() => setView("chat")} />
                        {allowed("invoices") && <Tool icon={Receipt} tone="amber" title="Xử lý hoá đơn" meta="Tải XML · Đối chiếu PO · Bút toán" onClick={() => setView("invoices")} />}
                        {allowed("books") && <Tool icon={BookOpenCheck} tone="blue" title="Sổ sách & biểu đồ" meta="CĐPS · Xu hướng · Cơ cấu chi phí" onClick={() => setView("books")} />}
                        {allowed("debts") && <Tool icon={HandCoins} tone="red" title="Công nợ & phiếu chi" meta="Tuổi nợ · Nhắc nợ · Lập phiếu chi" onClick={() => setView("debts")} />}
                        {allowed("sheets") && <Tool icon={FileSpreadsheet} tone="cyan" title="Phân tích Excel" meta="Tải file · Tổng theo nhóm · Biểu đồ" onClick={() => setView("sheets")} />}
                        {allowed("data") && <Tool icon={Database} tone="violet" title="Nhập dữ liệu" meta="Hệ thống TK · Sổ cái · Ngân sách" onClick={() => setView("data")} />}
                      </div>
                    </section>

                    <section className={styles.panel}>
                      <div className={styles.panelHeader}><div><h2>Hỏi nhanh</h2><p>Bấm để gửi ngay cho trợ lý</p></div></div>
                      <div className={styles.askRow}>
                        {SUGGESTIONS.map((text) => <button key={text} disabled={busy} onClick={() => void ask(text)}>{text}</button>)}
                      </div>
                    </section>

                    {expenses && (
                      <section className={styles.panel}>
                        <div className={styles.panelHeader}>
                          <div><h2>Chi phí {expenses.period === currentPeriod() ? "tháng này" : "tháng trước"}</h2><p>Theo tài khoản chi phí, từ sổ cái</p></div>
                          {allowed("books") && <button onClick={() => setView("books")}>Xem thêm<ChevronRight size={14} /></button>}
                        </div>
                        <div className={styles.chartSlot}>
                          {expenses.data.charts.length
                            ? <FinanceChart spec={expenses.data.charts[0]} height={240} />
                            : <div className={styles.empty}>Chưa có chi phí ghi sổ trong kỳ này.</div>}
                        </div>
                      </section>
                    )}
                  </div>

                  <aside className={styles.panel}>
                    <div className={styles.panelHeader}>
                      <div><h2>Sắp đến hạn trả</h2><p>Hoá đơn mua vào trong 30 ngày tới</p></div>
                      <button onClick={() => router.push("/calendar")} title="Mở lịch"><CalendarClock size={15} /></button>
                    </div>
                    {!schedule ? (
                      <div className={styles.empty}>{allowed("debts") ? "Đang tải…" : "Chức vụ của bạn chưa được xem công nợ."}</div>
                    ) : dueRows.length === 0 ? (
                      <div className={styles.empty}><ShieldCheck size={20} />Không có hoá đơn nào đến hạn.</div>
                    ) : (
                      <div className={styles.dueList}>
                        {dueRows.slice(0, 8).map((row) => {
                          const day = new Date(row.due_date);
                          return (
                            <button key={row.invoice_id} onClick={() => setView("debts")}>
                              <span className={`${styles.dateBox} ${row.overdue ? styles.overdue : ""}`}>{day.getDate()}<small>Th{day.getMonth() + 1}</small></span>
                              <span className={styles.dueInfo}><strong>{row.party}</strong><small>{row.series} số {row.number}{row.overdue ? " · quá hạn" : ""}</small></span>
                              <span className={styles.dueAmount}>{formatVnd(row.remaining)}</span>
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </aside>
                </div>
              </>
            )}

            {view === "chat" && (
              <section className={styles.chatWorkspace}>
                <header className={styles.chatHeader}>
                  <div>
                    <span className={`${styles.statIcon} ${styles.green}`}><Bot size={19} /></span>
                    <div><strong>Chat với Trợ lý Tài chính</strong><small>Số liệu lấy từ sổ sách; biểu đồ hiện kèm câu trả lời</small></div>
                  </div>
                  <button type="button" onClick={() => { setMessages([]); setConversationId(null); setQuestion(""); inputRef.current?.focus(); }}>
                    <MessageSquareText size={14} />Cuộc trò chuyện mới
                  </button>
                </header>

                <div className={styles.chatMessages}>
                  {messages.length === 0 && !busy && (
                    <div className={styles.chatEmpty}>
                      <span><Landmark size={28} /></span>
                      <h2>Bạn cần xem số liệu gì?</h2>
                      <p>Hỏi về số dư, ngân sách, công nợ, hoá đơn, hoặc file Excel bạn đã tải lên. Trợ lý chỉ soạn bản nháp khi bạn yêu cầu.</p>
                      <div className={styles.askRow} style={{ justifyContent: "center" }}>
                        {SUGGESTIONS.slice(0, 4).map((text) => <button key={text} onClick={() => void ask(text)}>{text}</button>)}
                      </div>
                    </div>
                  )}
                  {messages.map((message) => (
                    <ChatBubble
                      key={message.id}
                      message={message}
                      openApprovals={() => router.push("/approvals")}
                      openSheets={allowed("sheets") ? () => setView("sheets") : undefined}
                    />
                  ))}
                  {busy && (
                    <article className={styles.message}>
                      <span><Bot size={15} /></span>
                      <div className={styles.bubble}>
                        {streamed
                          ? <div className={styles.streaming}>{streamed}</div>
                          : <div className={styles.phase}><Loader2 size={14} className={styles.spin} />{phase ? PHASES[phase] : "Đang xử lý"}…</div>}
                      </div>
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
                      placeholder="Ví dụ: Số dư TK 112 cuối tháng trước? Lập phiếu chi trả hoá đơn 4501…"
                      aria-label="Câu hỏi cho Trợ lý Tài chính"
                    />
                    <button type="submit" disabled={busy || !question.trim()} aria-label="Gửi">
                      {busy ? <Loader2 size={17} className={styles.spin} /> : <Send size={17} />}
                    </button>
                  </form>
                  <div className={styles.composerHint}>Enter để gửi · Shift + Enter để xuống dòng</div>
                </div>
              </section>
            )}

            {view === "invoices" && allowed("invoices") && <InvoicesTab canUpload={can("finance.invoice.process")} canPropose={can("finance.journal.draft")} />}
            {view === "books" && allowed("books") && (
              <BooksTab period={currentPeriod()} canReadLedger={can("finance.ledger.view")} canSeeEntries={can("finance.journal.draft") || can("finance.ledger.view")} canSeeBudget={can("finance.ledger.view") || can("finance.budget.view_own")} />
            )}
            {view === "debts" && allowed("debts") && <DebtsTab canDraft={can("finance.journal.draft")} canRemind={can("finance.reminder.send")} />}
            {view === "sheets" && allowed("sheets") && <SheetsTab />}
            {view === "data" && allowed("data") && <DataTab />}
          </main>
        </div>
      </div>
    </div>
  );
}

function Tool({ icon: Icon, title, meta, tone, onClick }: { icon: typeof Receipt; title: string; meta: string; tone: string; onClick: () => void }) {
  return (
    <button className={styles.tool} onClick={onClick}>
      <span className={`${styles.toolIcon} ${styles[tone]}`}><Icon size={19} /></span>
      <span><strong>{title}</strong><small>{meta}</small></span>
      <ChevronRight size={16} />
    </button>
  );
}

/** One message: the answer, its charts, which books it read, and what to do next. */
function ChatBubble({ message, openApprovals, openSheets }: { message: Message; openApprovals: () => void; openSheets?: () => void }) {
  const mine = message.sender === "USER";
  const charts = (message.attachments ?? [])
    .filter((item) => item.type === "CHART_CARD" && item.payload)
    .map((item) => item.payload as unknown as ChartSpec);
  const tools = (message.tools_executed ?? [])
    .map((item) => item.tool_name || "")
    .filter((name) => TOOL_LABELS[name]);
  const drafted = tools.some((name) => DRAFTING.has(name));
  const usedSheet = tools.some((name) => SHEET_TOOLS.has(name));
  return (
    <article className={`${styles.message} ${mine ? styles.userMessage : ""} ${charts.length ? styles.wideMessage : ""}`}>
      <span>{mine ? "Bạn" : <Bot size={15} />}</span>
      <div className={styles.bubble} style={charts.length ? { flex: 1 } : undefined}>
        {mine ? <div>{message.content}</div> : <ChatMessageContent content={message.content} />}
        {charts.map((chart, index) => <FinanceChart key={`${chart.title}-${index}`} spec={chart} height={240} />)}
        {!mine && tools.length > 0 && (
          <div className={styles.toolTrail} aria-label="Nguồn số liệu">
            {Array.from(new Set(tools)).map((name) => <span key={name}>{TOOL_LABELS[name]}</span>)}
          </div>
        )}
        {!mine && (drafted || (usedSheet && openSheets)) && (
          <div className={styles.bubbleActions}>
            {drafted && <button type="button" onClick={openApprovals}><ShieldCheck size={13} />Xem phiếu đã gửi duyệt</button>}
            {usedSheet && openSheets && <button type="button" onClick={openSheets}><FileSpreadsheet size={13} />Mở file trong Phân tích Excel</button>}
          </div>
        )}
      </div>
    </article>
  );
}
