"use client";

import {
  AlertTriangle,
  ArrowRight,
  BellRing,
  Bot,
  BriefcaseBusiness,
  CalendarClock,
  ChevronRight,
  CircleGauge,
  ClipboardCheck,
  Download,
  HandCoins,
  ListChecks,
  Loader2,
  Lock,
  MessageSquareText,
  Save,
  Send,
  ShieldCheck,
  Sparkles,
  UserPlus,
  X,
} from "lucide-react";
import { KeyboardEvent, ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import ChatMessageContent from "@/components/chat/ChatMessageContent";
import { apiError, formatVnd } from "@/components/finance/format";
import api from "@/lib/api";
import { type ExecutionPhase, streamAgentChat } from "@/lib/chatStream";
import { useAuthStore, userCan } from "@/store/useAuthStore";
import styles from "./ceo.module.css";

type View = "overview" | "approvals" | "tasks" | "alerts" | "chat";
type Period = "TODAY" | "WEEK" | "MONTH" | "QUARTER";

interface Locked { permitted: false; needs?: string[]; message?: string }
type Maybe<T> = (T & { permitted?: true }) | Locked;
interface Change { change: string; change_percent: string | null; direction: string }
interface Results { periods: string; so_far: boolean; revenue: string; profit_before_tax: string; compared_with: string; revenue_vs_previous: Change; profit_vs_previous: Change; note?: string | null }
interface BudgetLine { department: string; account: string; budget: string; actual: string; over_amount: string; over_percent: string | null }
interface FinanceSection {
  permitted?: boolean;
  needs?: string[];
  results?: Maybe<Results>;
  over_budget?: { period: string; lines_over_budget: number; budget_lines: number; top: BudgetLine[] };
  receivables_overdue?: Maybe<{ total: string; invoices: number; customers: number; largest: { customer: string; overdue: string; oldest_days_overdue: number | null }[] }>;
  payables_due?: Maybe<{ within_days: number; until: string; invoices: number; already_overdue: number; still_to_schedule: string }>;
}
interface PeopleSection {
  headcount?: Maybe<{ active_employees: number; scope: string }>;
  on_leave?: Maybe<{ between: string; people: number; who: { name: string; from: string; to: string }[] }>;
  leave_waiting?: Maybe<{ requests: number }>;
  contracts_ending?: Maybe<{ within_days: number; contracts: number; who: { name: string; ends: string; type: string }[] }>;
}
interface LegalSection { scope: string; high_risk_unresolved: number; critical: number; top: { contract: string; risk: string; risk_score: number; reviewed_on: string }[] }
interface MarketingSection { scope: string; campaigns_waiting: number; by_stage: Record<string, number>; top: { campaign: string; stage: string }[] }
interface InboxItem { approval_id: string; title: string; kind: string; requester?: string | null; sent_by_you: boolean; risk: string; amount: string | null; waiting_days: number; sent_on: string; link: string }
interface Inbox { waiting_count: number; by_risk: Record<string, number>; oldest_waiting_days: number; items: InboxItem[] }
interface TasksSection { handed_out_open: number; handed_out_overdue: number; overdue: { task: string; assignee: string | null; due_date: string; days_overdue: number }[]; my_tasks_due_by_end_of_period: number }
interface AiCost { month: string; cost_usd: number; monthly_budget_usd: number; budget_used_percent: number; requests: number }
interface Briefing {
  period: string;
  today: string;
  from: string;
  to: string;
  sections: {
    finance?: FinanceSection;
    people?: PeopleSection;
    legal?: Maybe<LegalSection>;
    marketing?: Maybe<MarketingSection>;
    approvals?: Inbox;
    tasks?: TasksSection;
    ai_cost?: Maybe<AiCost>;
  };
}
interface TaskItem { task_id: string; title: string; assignee: string | null; assigned_by: string | null; status: string; priority: string; due_date: string | null; days_overdue: number; latest_comment: { by: string | null; text: string } | null }
interface TaskList { counts: { open: number; overdue: number; done: number }; items: TaskItem[] }
interface Person { id: string; name: string; label: string; email: string }
interface Alert { rule: string; subject: string; title: string; message: string; severity: string; link: string }
interface RuleSetting { key: string; label: string; description: string; unit: string; enabled: boolean; threshold: number; default: number; min: number; max: number }
interface Schedule { digest_enabled: boolean; digest_hour: number; digest_weekdays: number[]; alerts_enabled: boolean; last_digest_on: string | null; last_alert_check_at: string | null }
interface AlertsPayload { alerts: Alert[]; rules: RuleSetting[]; schedule: Schedule | null; can_manage: boolean }
interface Digest { today: string; title: string; lines: string[] }
interface Attachment { type: string; payload?: Record<string, unknown> }
interface ToolCall { tool_name?: string; status?: string }
interface Message { id: string; sender: "USER" | "ASSISTANT"; content: string; attachments?: Attachment[]; tools_executed?: ToolCall[] }
interface Conversation { id: string; agent_role: string }

const CEO_BOX = "executive.briefing.view";
const PERIODS: { key: Period; label: string }[] = [
  { key: "TODAY", label: "Hôm nay" },
  { key: "WEEK", label: "Tuần này" },
  { key: "MONTH", label: "Tháng này" },
  { key: "QUARTER", label: "Quý này" },
];
const VIEWS: { key: View; label: string; icon: typeof ListChecks }[] = [
  { key: "overview", label: "Bản tin", icon: CircleGauge },
  { key: "approvals", label: "Chờ tôi duyệt", icon: ClipboardCheck },
  { key: "tasks", label: "Giao & theo dõi việc", icon: ListChecks },
  { key: "alerts", label: "Cảnh báo & lịch", icon: BellRing },
  { key: "chat", label: "Chat với trợ lý", icon: MessageSquareText },
];
const WEEKDAYS = [
  { day: 1, label: "T2" }, { day: 2, label: "T3" }, { day: 3, label: "T4" }, { day: 4, label: "T5" },
  { day: 5, label: "T6" }, { day: 6, label: "T7" }, { day: 7, label: "CN" },
];
const PRIORITIES = [
  { key: "LOW", label: "Thấp" }, { key: "MEDIUM", label: "Bình thường" }, { key: "HIGH", label: "Cao" }, { key: "URGENT", label: "Khẩn" },
];
const PHASES: Record<ExecutionPhase, string> = {
  ANALYZING: "Đang phân tích câu hỏi",
  SEARCHING: "Đang tìm tài liệu",
  TOOL_CALLING: "Đang đọc số liệu các phòng",
  WAITING_APPROVAL: "Đang chờ phê duyệt",
  COMPLETED: "Xong",
};
const TOOL_LABELS: Record<string, string> = {
  executive_briefing: "Bản tin điều hành",
  list_my_approvals: "Hộp phê duyệt",
  compare_income_statements: "So sánh kết quả kinh doanh",
  get_income_statement: "Kết quả kinh doanh",
  get_expense_breakdown: "Cơ cấu chi phí",
  get_account_trend: "Xu hướng tài khoản",
  budget_vs_actual: "Ngân sách",
  ar_ap_aging: "Tuổi nợ",
  payment_schedule: "Lịch trả tiền",
  list_contract_reviews: "Hợp đồng đã rà soát",
  list_marketing_campaigns: "Chiến dịch marketing",
  expense_lookup: "Chi phí AI",
  assign_task: "Giao việc",
  list_tasks: "Theo dõi việc",
  rag_search: "Tra tài liệu",
};
const SUGGESTIONS = [
  "Hôm nay công ty thế nào?",
  "Lợi nhuận quý này so với quý trước?",
  "Có gì đang chờ tôi duyệt, cái nào gấp?",
  "Những việc tôi giao tuần trước đến đâu rồi?",
];
const RISK_CLASS: Record<string, string> = { "rất cao": "pillCritical", cao: "pillHigh", "trung bình": "pillMedium", "thấp": "pillLow" };

function isLocked<T>(value: Maybe<T> | undefined | null): value is Locked {
  return !!value && (value as Locked).permitted === false;
}
function viDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [year, month, day] = iso.slice(0, 10).split("-");
  return day && month && year ? `${day}/${month}/${year}` : iso;
}
/** "2026-09..2026-09" → "09/2026"; "2026-07..2026-09" → "07/2026 – 09/2026". */
function months(range: string): string {
  const [from, to] = range.split("..").map((part) => (part.length >= 7 ? `${part.slice(5, 7)}/${part.slice(0, 4)}` : part));
  return !to || from === to ? from : `${from} – ${to}`;
}
function percent(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  return `${Number(value).toLocaleString("vi-VN", { maximumFractionDigits: 1 })}%`;
}
function todayIso(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

/**
 * The CEO assistant's workspace. Everything on it comes from /api/v1/executive, the same
 * functions the assistant's tools call, so the page and the chat never disagree; each
 * section still keeps its department's permission and says when it is withheld.
 */
export default function CeoAgentPage() {
  const router = useRouter();
  const { user, isAuthenticated, hasHydrated } = useAuthStore();
  const allowed = userCan(user, CEO_BOX);
  const [view, setView] = useState<View>("overview");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [period, setPeriod] = useState<Period>("TODAY");
  const [briefing, setBriefing] = useState<Briefing | null>(null);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [inbox, setInbox] = useState<Inbox | null>(null);
  const [alerts, setAlerts] = useState<AlertsPayload | null>(null);

  const load = useCallback(async (which: Period) => {
    setLoading(true);
    try {
      const [brief, waiting, found] = await Promise.all([
        api.get<Briefing>("/api/v1/executive/briefing", { params: { period: which } }),
        api.get<Inbox>("/api/v1/executive/approvals"),
        api.get<AlertsPayload>("/api/v1/executive/alerts"),
      ]);
      setBriefing(brief.data);
      setInbox(waiting.data);
      setAlerts(found.data);
    } catch (reason) {
      setError(apiError(reason).message);
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
    if (!allowed) return;
    const timer = window.setTimeout(() => {
      // A notification opens the page on the view it is about (?view=alerts).
      const wanted = new URLSearchParams(window.location.search).get("view");
      if (wanted && VIEWS.some((item) => item.key === wanted)) setView(wanted as View);
      void load(period);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [allowed, hasHydrated, isAuthenticated, load, period, router]);

  const exportBriefing = async () => {
    setExporting(true);
    try {
      const response = await api.get<Blob>("/api/v1/executive/briefing/export", { params: { period }, responseType: "blob" });
      const url = URL.createObjectURL(response.data);
      const link = document.createElement("a");
      link.href = url;
      link.download = `ban-tin-dieu-hanh-${briefing?.from ?? todayIso()}.xlsx`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (reason) {
      setError(apiError(reason).message);
    } finally {
      setExporting(false);
    }
  };

  if (!hasHydrated || !isAuthenticated) return null;

  const sections = briefing?.sections ?? {};
  const receivable = sections.finance?.receivables_overdue;
  const alertCount = alerts?.alerts.length ?? null;

  return (
    <div className={styles.page}>
      <Sidebar agentStatuses={{ CEO: true }} />
      <div className={styles.shell}>
        <header className={styles.topbar}>
          <div className={styles.breadcrumb}><span>Nhân viên AI</span><ChevronRight size={14} /><strong>Trợ lý CEO</strong></div>
          <div className={styles.topActions}>
            <button type="button" className={styles.iconButton} title="Trung tâm phê duyệt" onClick={() => router.push("/approvals")}><ShieldCheck size={17} /></button>
            <span className={styles.status}><span />Đang hoạt động</span>
          </div>
        </header>

        <div className={styles.workspace}>
          <aside className={styles.rail}>
            <div className={styles.agentIdentity}>
              <div className={styles.agentMark}><BriefcaseBusiness size={20} /></div>
              <div><strong>Trợ lý CEO</strong><span>Điều hành & phê duyệt</span></div>
            </div>
            <nav className={styles.nav} aria-label="Không gian Trợ lý CEO">
              {VIEWS.map(({ key, label, icon: Icon }) => (
                <button key={key} className={view === key ? styles.active : ""} onClick={() => setView(key)} aria-current={view === key ? "page" : undefined} disabled={!allowed}>
                  <Icon size={17} />{label}
                  {key === "approvals" && !!inbox?.waiting_count && <span className={styles.navBadge}>{inbox.waiting_count}</span>}
                  {key === "alerts" && !!alertCount && <span className={styles.navBadge}>{alertCount}</span>}
                </button>
              ))}
            </nav>
            <div className={styles.railNote}>
              <strong><ShieldCheck size={14} />Không duyệt thay người</strong>
              Trợ lý tổng hợp, xếp ưu tiên và giao việc. Mọi quyết định duyệt vẫn do người có quyền bấm trên trang Phê duyệt.
            </div>
          </aside>

          <main className={styles.main}>
            {error && <div className={styles.error}><AlertTriangle size={17} /><span>{error}</span><button onClick={() => setError(null)} title="Đóng"><X size={15} /></button></div>}
            {notice && <div className={styles.notice}><ShieldCheck size={17} /><span>{notice}</span><button onClick={() => setNotice(null)} title="Đóng"><X size={15} /></button></div>}

            {!allowed ? (
              <section className={styles.panel}>
                <div className={styles.empty}>
                  <Lock size={22} />
                  <strong>Chức vụ của bạn chưa được dùng Trợ lý CEO</strong>
                  <span>Hãy nhờ quản trị viên tích quyền “Dùng Trợ lý CEO” ở trang Cơ cấu tổ chức.</span>
                </div>
              </section>
            ) : (
              <>
                {view === "overview" && (
                  <>
                    <div className={styles.headingRow}>
                      <div>
                        <span className={styles.eyebrow}>BẢN TIN ĐIỀU HÀNH</span>
                        <h1>Công ty {briefing?.period ?? "hôm nay"}</h1>
                        <p>{briefing ? `Số liệu từ ${viDate(briefing.from)} đến ${viDate(briefing.to)}, lấy trực tiếp từ dữ liệu các phòng.` : "Đang tổng hợp số liệu các phòng…"}</p>
                      </div>
                      <div className={styles.headActions}>
                        <div className={styles.periodTabs} role="group" aria-label="Kỳ báo cáo">
                          {PERIODS.map((item) => (
                            <button key={item.key} type="button" aria-pressed={period === item.key} onClick={() => setPeriod(item.key)}>{item.label}</button>
                          ))}
                        </div>
                        <button type="button" className={styles.secondaryButton} onClick={() => void exportBriefing()} disabled={exporting || !briefing}>
                          {exporting ? <Loader2 size={15} className={styles.spin} /> : <Download size={15} />}Xuất Excel
                        </button>
                        <button type="button" className={styles.primaryButton} onClick={() => setView("chat")}><Sparkles size={16} />Hỏi trợ lý</button>
                      </div>
                    </div>

                    <section className={styles.stats}>
                      <button className={styles.stat} onClick={() => setView("approvals")}>
                        <span className={`${styles.statIcon} ${styles.violet}`}><ClipboardCheck size={19} /></span>
                        <div><strong>{inbox?.waiting_count ?? "—"}</strong><small>Yêu cầu chờ bạn duyệt</small></div><ArrowRight size={15} />
                      </button>
                      <button className={styles.stat} onClick={() => setView("alerts")}>
                        <span className={`${styles.statIcon} ${styles.amber}`}><BellRing size={19} /></span>
                        <div><strong>{alertCount ?? "—"}</strong><small>Cảnh báo đang mở</small></div><ArrowRight size={15} />
                      </button>
                      <button className={styles.stat} onClick={() => router.push("/agents/FINANCE")} disabled={!receivable || isLocked(receivable)}>
                        <span className={`${styles.statIcon} ${styles.red}`}><HandCoins size={19} /></span>
                        <div><strong>{receivable && !isLocked(receivable) ? formatVnd(receivable.total) : "—"}</strong><small>Khách hàng nợ quá hạn</small></div><ArrowRight size={15} />
                      </button>
                      <button className={styles.stat} onClick={() => setView("tasks")}>
                        <span className={`${styles.statIcon} ${styles.blue}`}><ListChecks size={19} /></span>
                        <div><strong>{sections.tasks?.handed_out_overdue ?? "—"}</strong><small>Việc bạn giao đã trễ hạn</small></div><ArrowRight size={15} />
                      </button>
                    </section>

                    {loading && !briefing ? (
                      <section className={styles.panel}><div className={styles.empty}><Loader2 size={20} className={styles.spin} />Đang tổng hợp bản tin…</div></section>
                    ) : briefing && (
                      <div className={styles.sectionGrid}>
                        <FinancePanel section={sections.finance} />
                        <PeoplePanel section={sections.people} />
                        <ApprovalsPanel inbox={inbox} open={() => setView("approvals")} />
                        <TasksPanel section={sections.tasks} open={() => setView("tasks")} />
                        <LegalPanel section={sections.legal} />
                        <MarketingPanel section={sections.marketing} />
                        <AiCostPanel section={sections.ai_cost} />
                        <section className={styles.panel}>
                          <div className={styles.panelHeader}><div><h2>Hỏi nhanh</h2><p>Bấm để hỏi trợ lý ngay</p></div></div>
                          <div className={styles.askRow}>
                            {SUGGESTIONS.map((text) => <button key={text} onClick={() => { setView("chat"); window.dispatchEvent(new CustomEvent("ceo-ask", { detail: text })); }}>{text}</button>)}
                          </div>
                        </section>
                      </div>
                    )}
                  </>
                )}

                {view === "approvals" && <ApprovalsView inbox={inbox} reload={() => void load(period)} />}
                {view === "tasks" && <TasksView onNotice={setNotice} onError={setError} onChanged={() => void load(period)} />}
                {view === "alerts" && <AlertsView payload={alerts} onSaved={(next) => setAlerts(next)} onNotice={setNotice} onError={setError} />}
                <ChatView visible={view === "chat"} onError={setError} />
              </>
            )}
          </main>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ overview panels */

function Panel({ title, hint, action, children }: { title: string; hint?: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section className={styles.panel}>
      <div className={styles.panelHeader}><div><h2>{title}</h2>{hint && <p>{hint}</p>}</div>{action}</div>
      <div className={styles.sectionBody}>{children}</div>
    </section>
  );
}

function LockedNote({ value }: { value: Locked }) {
  return (
    <div className={styles.locked}>
      <Lock size={14} />
      <span>Chức vụ của bạn chưa được xem mục này{value.needs?.length ? ` — cần quyền: ${value.needs.join(", ")}` : ""}.</span>
    </div>
  );
}

function ChangeTag({ change }: { change?: Change }) {
  if (!change) return null;
  const tone = change.direction === "tăng" ? styles.up : change.direction === "giảm" ? styles.down : styles.flat;
  return <span className={`${styles.change} ${tone}`}>{change.direction}{change.change_percent !== null ? ` ${percent(change.change_percent)}` : ""}</span>;
}

function FinancePanel({ section }: { section?: FinanceSection }) {
  const router = useRouter();
  if (!section) return null;
  const open = <button onClick={() => router.push("/agents/FINANCE")}>Trợ lý Tài chính<ChevronRight size={14} /></button>;
  if (section.permitted === false) return <Panel title="Tài chính" action={open}><LockedNote value={section as Locked} /></Panel>;
  const { results, over_budget: budget, receivables_overdue: receivable, payables_due: payable } = section;
  return (
    <Panel title="Tài chính" hint={results && !isLocked(results) ? `Kỳ ${months(results.periods)}${results.so_far ? " (đến nay)" : ""}` : "Từ sổ sách kế toán"} action={open}>
      {results && (isLocked(results) ? <LockedNote value={results} /> : (
        <dl className={styles.kv}>
          <dt>Doanh thu thuần</dt><dd>{formatVnd(results.revenue)}<ChangeTag change={results.revenue_vs_previous} /></dd>
          <dt>Lợi nhuận trước thuế</dt><dd>{formatVnd(results.profit_before_tax)}<ChangeTag change={results.profit_vs_previous} /></dd>
          <dt>So với kỳ</dt><dd>{months(results.compared_with)}</dd>
        </dl>
      ))}
      {budget && (
        <>
          <p className={styles.subhead}>Ngân sách {months(budget.period)}: {budget.lines_over_budget}/{budget.budget_lines} dòng vượt</p>
          {budget.top.length > 0 && (
            <ul className={styles.miniList}>
              {budget.top.map((row) => <li key={`${row.department}-${row.account}`}><span>{row.department} · TK {row.account}</span><span>+{formatVnd(row.over_amount)}{row.over_percent ? ` (${percent(row.over_percent)})` : ""}</span></li>)}
            </ul>
          )}
        </>
      )}
      {receivable && (isLocked(receivable) ? <LockedNote value={receivable} /> : (
        <>
          <p className={styles.subhead}>Nợ phải thu quá hạn: {formatVnd(receivable.total)} · {receivable.customers} khách</p>
          {receivable.largest.length > 0 && (
            <ul className={styles.miniList}>
              {receivable.largest.map((row) => <li key={row.customer}><span>{row.customer}{row.oldest_days_overdue ? ` · ${row.oldest_days_overdue} ngày` : ""}</span><span>{formatVnd(row.overdue)}</span></li>)}
            </ul>
          )}
        </>
      ))}
      {payable && !isLocked(payable) && (
        <p className={styles.muted}>Phải trả trong {payable.within_days} ngày: {payable.invoices} hoá đơn{payable.already_overdue ? `, ${payable.already_overdue} đã quá hạn` : ""}; còn {formatVnd(payable.still_to_schedule)} chưa lên lịch chi.</p>
      )}
    </Panel>
  );
}

function PeoplePanel({ section }: { section?: PeopleSection }) {
  if (!section) return null;
  const { headcount, on_leave: leave, leave_waiting: waiting, contracts_ending: ending } = section;
  return (
    <Panel title="Nhân sự" hint="Trong phạm vi bạn được xem">
      <dl className={styles.kv}>
        <dt>Nhân sự đang làm việc</dt><dd>{headcount && !isLocked(headcount) ? headcount.active_employees : "—"}</dd>
        <dt>Đang nghỉ trong kỳ</dt><dd>{leave && !isLocked(leave) ? leave.people : "—"}</dd>
        <dt>Đơn nghỉ chờ duyệt</dt><dd>{waiting && !isLocked(waiting) ? waiting.requests : "—"}</dd>
        <dt>HĐLĐ sắp hết hạn</dt><dd>{ending && !isLocked(ending) ? ending.contracts : "—"}</dd>
      </dl>
      {leave && !isLocked(leave) && leave.who.length > 0 && (
        <>
          <p className={styles.subhead}>Ai nghỉ</p>
          <ul className={styles.miniList}>{leave.who.slice(0, 5).map((item) => <li key={`${item.name}-${item.from}`}><span>{item.name}</span><span>{viDate(item.from)} – {viDate(item.to)}</span></li>)}</ul>
        </>
      )}
      {ending && !isLocked(ending) && ending.who.length > 0 && (
        <>
          <p className={styles.subhead}>Hợp đồng hết hạn trong {ending.within_days} ngày</p>
          <ul className={styles.miniList}>{ending.who.map((item) => <li key={`${item.name}-${item.ends}`}><span>{item.name}</span><span>{viDate(item.ends)}</span></li>)}</ul>
        </>
      )}
      {(isLocked(headcount) || isLocked(leave) || isLocked(ending)) && (
        <p className={styles.muted}>Mục ghi “—” là mục chức vụ của bạn chưa được xem.</p>
      )}
    </Panel>
  );
}

function ApprovalsPanel({ inbox, open }: { inbox: Inbox | null; open: () => void }) {
  return (
    <Panel title="Chờ bạn duyệt" hint={inbox ? `Lâu nhất ${inbox.oldest_waiting_days} ngày` : undefined} action={<button onClick={open}>Xem tất cả<ChevronRight size={14} /></button>}>
      {!inbox ? <p className={styles.muted}>Đang tải…</p> : inbox.items.length === 0 ? (
        <p className={styles.muted}>Không có yêu cầu nào chờ bạn quyết định.</p>
      ) : (
        <ul className={styles.miniList}>
          {inbox.items.slice(0, 5).map((item) => (
            <li key={item.approval_id}><span>{item.kind}: {item.title}</span><span className={`${styles.pill} ${styles[RISK_CLASS[item.risk] ?? "pillMedium"]}`}>{item.risk}</span></li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function TasksPanel({ section, open }: { section?: TasksSection; open: () => void }) {
  if (!section) return null;
  return (
    <Panel title="Việc bạn giao" hint={`${section.handed_out_open} việc đang mở · ${section.my_tasks_due_by_end_of_period} việc của bạn đến hạn trong kỳ`} action={<button onClick={open}>Giao việc<ChevronRight size={14} /></button>}>
      {section.overdue.length === 0 ? <p className={styles.muted}>Không có việc nào trễ hạn.</p> : (
        <ul className={styles.miniList}>
          {section.overdue.map((item) => <li key={`${item.task}-${item.due_date}`}><span>{item.task} · {item.assignee ?? "chưa có người nhận"}</span><span className={styles.down}>trễ {item.days_overdue} ngày</span></li>)}
        </ul>
      )}
    </Panel>
  );
}

function LegalPanel({ section }: { section?: Maybe<LegalSection> }) {
  const router = useRouter();
  if (!section) return null;
  const open = <button onClick={() => router.push("/agents/LEGAL")}>Trợ lý Pháp lý<ChevronRight size={14} /></button>;
  if (isLocked(section)) return <Panel title="Pháp chế" action={open}><LockedNote value={section} /></Panel>;
  return (
    <Panel title="Pháp chế" hint={`Hợp đồng rủi ro cao chưa xử lý · ${section.scope}`} action={open}>
      {section.top.length === 0 ? <p className={styles.muted}>Không có hợp đồng rủi ro cao nào còn dở.</p> : (
        <ul className={styles.miniList}>
          {section.top.map((item) => <li key={`${item.contract}-${item.reviewed_on}`}><span>{item.contract}</span><span className={`${styles.pill} ${item.risk === "rất cao" ? styles.pillCritical : styles.pillHigh}`}>{item.risk} · {item.risk_score}</span></li>)}
        </ul>
      )}
    </Panel>
  );
}

function MarketingPanel({ section }: { section?: Maybe<MarketingSection> }) {
  const router = useRouter();
  if (!section) return null;
  const open = <button onClick={() => router.push("/agents/MARKETING")}>Trợ lý Marketing<ChevronRight size={14} /></button>;
  if (isLocked(section)) return <Panel title="Marketing" action={open}><LockedNote value={section} /></Panel>;
  return (
    <Panel title="Marketing" hint={`${section.campaigns_waiting} chiến dịch đang chờ · ${section.scope}`} action={open}>
      {section.top.length === 0 ? <p className={styles.muted}>Không có chiến dịch nào đang chờ duyệt.</p> : (
        <ul className={styles.miniList}>
          {section.top.map((item) => <li key={`${item.campaign}-${item.stage}`}><span>{item.campaign}</span><span>{item.stage}</span></li>)}
        </ul>
      )}
    </Panel>
  );
}

function AiCostPanel({ section }: { section?: Maybe<AiCost> }) {
  if (!section) return null;
  if (isLocked(section)) return <Panel title="Chi phí AI"><LockedNote value={section} /></Panel>;
  return (
    <Panel title="Chi phí AI" hint={`Tháng ${section.month.slice(5)}/${section.month.slice(0, 4)}`}>
      <dl className={styles.kv}>
        <dt>Đã chi</dt><dd>{section.cost_usd.toLocaleString("vi-VN", { maximumFractionDigits: 4 })} USD</dd>
        <dt>Lượt gọi mô hình</dt><dd>{section.requests.toLocaleString("vi-VN")}</dd>
        <dt>Ngân sách AI tháng</dt><dd>{section.monthly_budget_usd ? `${section.monthly_budget_usd.toLocaleString("vi-VN")} USD (${percent(section.budget_used_percent)})` : "Chưa đặt"}</dd>
      </dl>
    </Panel>
  );
}

/* ---------------------------------------------------------------------- approvals view */

function ApprovalsView({ inbox, reload }: { inbox: Inbox | null; reload: () => void }) {
  const router = useRouter();
  return (
    <>
      <div className={styles.headingRow}>
        <div><span className={styles.eyebrow}>HỘP PHÊ DUYỆT</span><h1>Chờ bạn quyết định</h1><p>Xếp theo mức rủi ro, rồi số tiền, rồi thời gian đã chờ. Bấm để mở và duyệt trên trang Phê duyệt.</p></div>
        <button type="button" className={styles.secondaryButton} onClick={reload}>Làm mới</button>
      </div>
      <section className={styles.panel}>
        <div className={styles.toolbar}>
          <span className={styles.muted}>{inbox ? `${inbox.waiting_count} yêu cầu` : "Đang tải…"}</span>
          <div className={styles.segmented}>
            {Object.entries(inbox?.by_risk ?? {}).map(([risk, count]) => <span key={risk} className={`${styles.pill} ${styles[RISK_CLASS[risk] ?? "pillMedium"]}`}>{risk}: {count}</span>)}
          </div>
        </div>
        {inbox && inbox.items.length === 0 ? (
          <div className={styles.empty}><ShieldCheck size={22} />Không có yêu cầu nào chờ bạn duyệt.</div>
        ) : (
          <div className={styles.rows}>
            {(inbox?.items ?? []).map((item) => (
              <div key={item.approval_id} className={styles.row}>
                <div className={styles.rowMain}>
                  <strong>{item.title}</strong>
                  <div className={styles.rowMeta}>
                    <span className={`${styles.pill} ${styles[RISK_CLASS[item.risk] ?? "pillMedium"]}`}>rủi ro {item.risk}</span>
                    <span>{item.kind}</span>
                    <span>{item.sent_by_you ? "Bạn gửi" : `Người gửi: ${item.requester ?? "—"}`}</span>
                    <span>Gửi {viDate(item.sent_on)} · chờ {item.waiting_days} ngày</span>
                  </div>
                </div>
                <div className={styles.rowSide}>
                  {item.amount && <span className={styles.amount}>{formatVnd(item.amount)}</span>}
                  <button type="button" className={styles.linkButton} onClick={() => router.push(item.link)}>Mở để quyết định<ArrowRight size={13} /></button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </>
  );
}

/* -------------------------------------------------------------------------- tasks view */

function TasksView({ onNotice, onError, onChanged }: { onNotice: (text: string) => void; onError: (text: string) => void; onChanged: () => void }) {
  const [scope, setScope] = useState<"ASSIGNED_BY_ME" | "ASSIGNED_TO_ME">("ASSIGNED_BY_ME");
  const [status, setStatus] = useState<"OPEN" | "OVERDUE" | "DONE" | "ALL">("OPEN");
  const [list, setList] = useState<TaskList | null>(null);
  const [people, setPeople] = useState<Person[]>([]);
  const [form, setForm] = useState({ assignee_id: "", title: "", description: "", due_date: "", priority: "MEDIUM" });
  const [saving, setSaving] = useState(false);

  const loadList = useCallback(async () => {
    try {
      const { data } = await api.get<TaskList>("/api/v1/executive/tasks", { params: { scope, status } });
      setList(data);
    } catch (reason) {
      onError(apiError(reason).message);
    }
  }, [onError, scope, status]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadList(), 0);
    return () => window.clearTimeout(timer);
  }, [loadList]);

  useEffect(() => {
    let active = true;
    api.get<Person[]>("/api/v1/executive/people")
      .then(({ data }) => { if (active) setPeople(data); })
      .catch((reason) => onError(apiError(reason).message));
    return () => { active = false; };
  }, [onError]);

  const submit = async () => {
    if (!form.assignee_id || !form.title.trim()) return;
    setSaving(true);
    try {
      const { data } = await api.post<{ reply: string }>("/api/v1/executive/tasks", {
        assignee_id: form.assignee_id,
        title: form.title.trim(),
        description: form.description.trim() || null,
        due_date: form.due_date || null,
        priority: form.priority,
      });
      onNotice(data.reply);
      setForm({ assignee_id: "", title: "", description: "", due_date: "", priority: "MEDIUM" });
      setScope("ASSIGNED_BY_ME");
      void loadList();
      onChanged();
    } catch (reason) {
      onError(apiError(reason).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <div className={styles.headingRow}>
        <div><span className={styles.eyebrow}>GIAO VIỆC & THEO DÕI</span><h1>Công việc</h1><p>Việc bạn giao và việc giao cho bạn, kèm bình luận mới nhất. Người nhận được báo ngay khi bạn giao.</p></div>
      </div>
      <div className={styles.dashboardGrid}>
        <section className={styles.panel}>
          <div className={styles.toolbar}>
            <div className={styles.segmented} role="group" aria-label="Phạm vi">
              <button type="button" aria-pressed={scope === "ASSIGNED_BY_ME"} onClick={() => setScope("ASSIGNED_BY_ME")}>Việc tôi giao</button>
              <button type="button" aria-pressed={scope === "ASSIGNED_TO_ME"} onClick={() => setScope("ASSIGNED_TO_ME")}>Việc của tôi</button>
            </div>
            <div className={styles.segmented} role="group" aria-label="Trạng thái">
              {([["OPEN", `Đang mở (${list?.counts.open ?? 0})`], ["OVERDUE", `Trễ hạn (${list?.counts.overdue ?? 0})`], ["DONE", `Đã xong (${list?.counts.done ?? 0})`], ["ALL", "Tất cả"]] as const).map(([key, label]) => (
                <button key={key} type="button" aria-pressed={status === key} onClick={() => setStatus(key)}>{label}</button>
              ))}
            </div>
          </div>
          {!list ? <div className={styles.empty}><Loader2 size={18} className={styles.spin} />Đang tải…</div> : list.items.length === 0 ? (
            <div className={styles.empty}><ListChecks size={22} />Không có việc nào trong mục này.</div>
          ) : (
            <div className={styles.rows}>
              {list.items.map((task) => (
                <div key={task.task_id} className={styles.row}>
                  <div className={styles.rowMain}>
                    <strong>{task.title}</strong>
                    <div className={styles.rowMeta}>
                      <span className={`${styles.pill} ${task.status === "quá hạn" ? styles.pillCritical : task.status === "đã xong" ? styles.pillDone : styles.pillMedium}`}>{task.status}{task.days_overdue ? ` ${task.days_overdue} ngày` : ""}</span>
                      <span>{scope === "ASSIGNED_BY_ME" ? `Người nhận: ${task.assignee ?? "—"}` : `Người giao: ${task.assigned_by ?? "—"}`}</span>
                      <span>Hạn: {viDate(task.due_date)}</span>
                      <span>Ưu tiên: {task.priority}</span>
                    </div>
                    {task.latest_comment && <span className={styles.comment}>{task.latest_comment.by ?? "Ai đó"}: “{task.latest_comment.text}”</span>}
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className={styles.panel}>
          <div className={styles.panelHeader}><div><h2>Giao việc mới</h2><p>Theo đúng luật của trang Công việc</p></div><UserPlus size={18} color="#7f8b9b" /></div>
          <form className={styles.form} onSubmit={(event) => { event.preventDefault(); void submit(); }}>
            <div className={styles.field}>
              <label htmlFor="ceo-assignee">Người nhận</label>
              <select id="ceo-assignee" value={form.assignee_id} onChange={(event) => setForm({ ...form, assignee_id: event.target.value })} required>
                <option value="">Chọn người trong danh bạ…</option>
                {people.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
              </select>
            </div>
            <div className={styles.field}>
              <label htmlFor="ceo-title">Việc cần làm</label>
              <input id="ceo-title" value={form.title} maxLength={255} onChange={(event) => setForm({ ...form, title: event.target.value })} placeholder="Ví dụ: Chuẩn bị báo cáo công nợ quá hạn" required />
            </div>
            <div className={styles.field}>
              <label htmlFor="ceo-description">Mô tả (không bắt buộc)</label>
              <textarea id="ceo-description" value={form.description} maxLength={5000} onChange={(event) => setForm({ ...form, description: event.target.value })} />
            </div>
            <div className={styles.formRow}>
              <div className={styles.field}>
                <label htmlFor="ceo-due">Hạn hoàn thành</label>
                <input id="ceo-due" type="date" min={todayIso()} value={form.due_date} onChange={(event) => setForm({ ...form, due_date: event.target.value })} />
              </div>
              <div className={styles.field}>
                <label htmlFor="ceo-priority">Mức ưu tiên</label>
                <select id="ceo-priority" value={form.priority} onChange={(event) => setForm({ ...form, priority: event.target.value })}>
                  {PRIORITIES.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}
                </select>
              </div>
            </div>
            <div className={styles.formActions}>
              <button type="submit" className={styles.primaryButton} disabled={saving || !form.assignee_id || !form.title.trim()}>
                {saving ? <Loader2 size={15} className={styles.spin} /> : <Send size={15} />}Giao việc
              </button>
            </div>
          </form>
        </section>
      </div>
    </>
  );
}

/* ------------------------------------------------------------------------- alerts view */

function AlertsView({ payload, onSaved, onNotice, onError }: {
  payload: AlertsPayload | null;
  onSaved: (next: AlertsPayload) => void;
  onNotice: (text: string) => void;
  onError: (text: string) => void;
}) {
  const router = useRouter();
  const [rules, setRules] = useState<RuleSetting[]>(payload?.rules ?? []);
  const [schedule, setSchedule] = useState<Schedule | null>(payload?.schedule ?? null);
  const [digest, setDigest] = useState<Digest | null>(null);
  const [saving, setSaving] = useState(false);
  const canManage = !!payload?.can_manage;

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setRules(payload?.rules ?? []);
      setSchedule(payload?.schedule ?? null);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [payload]);

  useEffect(() => {
    let active = true;
    api.get<Digest>("/api/v1/executive/digest")
      .then(({ data }) => { if (active) setDigest(data); })
      .catch((reason) => onError(apiError(reason).message));
    return () => { active = false; };
  }, [onError]);

  const save = async () => {
    setSaving(true);
    try {
      const { data } = await api.put<Omit<AlertsPayload, "alerts">>("/api/v1/executive/alert-settings", {
        rules: rules.map(({ key, enabled, threshold }) => ({ key, enabled, threshold: Number(threshold) })),
        schedule: schedule && {
          digest_enabled: schedule.digest_enabled,
          digest_hour: schedule.digest_hour,
          digest_weekdays: schedule.digest_weekdays,
          alerts_enabled: schedule.alerts_enabled,
        },
      });
      const fresh = await api.get<AlertsPayload>("/api/v1/executive/alerts");
      onSaved({ ...fresh.data, ...data });
      onNotice("Đã lưu cấu hình cảnh báo và lịch bản tin.");
    } catch (reason) {
      onError(apiError(reason).message);
    } finally {
      setSaving(false);
    }
  };

  const toggleDay = (day: number) => {
    if (!schedule) return;
    const days = schedule.digest_weekdays.includes(day) ? schedule.digest_weekdays.filter((item) => item !== day) : [...schedule.digest_weekdays, day].sort();
    setSchedule({ ...schedule, digest_weekdays: days });
  };

  return (
    <>
      <div className={styles.headingRow}>
        <div><span className={styles.eyebrow}>CẢNH BÁO CHỦ ĐỘNG</span><h1>Cảnh báo & bản tin định kỳ</h1><p>Luật chạy bằng số liệu thật, theo đúng quyền xem của từng người nhận. Cảnh báo mới được gửi vào thông báo, mỗi việc tối đa một lần một ngày.</p></div>
        {canManage && (
          <button type="button" className={styles.primaryButton} onClick={() => void save()} disabled={saving}>
            {saving ? <Loader2 size={15} className={styles.spin} /> : <Save size={15} />}Lưu cấu hình
          </button>
        )}
      </div>

      <div className={styles.dashboardGrid}>
        <div style={{ display: "grid", gap: 14, minWidth: 0 }}>
          <section className={styles.panel}>
            <div className={styles.panelHeader}><div><h2>Cảnh báo đang mở</h2><p>Kiểm tra lúc bạn mở trang này</p></div></div>
            {!payload ? <div className={styles.empty}><Loader2 size={18} className={styles.spin} />Đang kiểm tra…</div> : payload.alerts.length === 0 ? (
              <div className={styles.empty}><ShieldCheck size={22} />Không có cảnh báo nào theo các luật đang bật.</div>
            ) : (
              <div className={styles.alertList}>
                {payload.alerts.map((alert) => (
                  <div key={`${alert.rule}-${alert.subject}`} className={`${styles.alert} ${alert.severity === "CRITICAL" ? styles.alertCritical : ""}`}>
                    <div><strong>{alert.title}</strong><p>{alert.message}</p></div>
                    <button type="button" className={styles.linkButton} onClick={() => router.push(alert.link)}>Mở<ArrowRight size={13} /></button>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className={styles.panel}>
            <div className={styles.panelHeader}><div><h2>Luật cảnh báo</h2><p>{canManage ? "Bật, tắt và đặt ngưỡng cho công ty" : "Chỉ chức vụ có quyền “Cấu hình cảnh báo điều hành” mới đổi được"}</p></div></div>
            <div className={styles.ruleTable}>
              {rules.map((rule, index) => (
                <div key={rule.key} className={styles.rule}>
                  <label className={styles.switch} title={rule.enabled ? "Đang bật" : "Đang tắt"}>
                    <input type="checkbox" checked={rule.enabled} disabled={!canManage} aria-label={`Bật luật ${rule.label}`}
                      onChange={(event) => setRules(rules.map((item, at) => at === index ? { ...item, enabled: event.target.checked } : item))} />
                    <span />
                  </label>
                  <div><strong>{rule.label}</strong><small>{rule.description}</small></div>
                  <div className={styles.threshold}>
                    <input type="number" min={rule.min} max={rule.max} value={rule.threshold} disabled={!canManage} aria-label={`Ngưỡng của ${rule.label}`}
                      onChange={(event) => setRules(rules.map((item, at) => at === index ? { ...item, threshold: Number(event.target.value) } : item))} />
                    <span>{rule.unit}</span>
                  </div>
                </div>
              ))}
            </div>
          </section>
        </div>

        <div style={{ display: "grid", gap: 14, minWidth: 0 }}>
          <section className={styles.panel}>
            <div className={styles.panelHeader}><div><h2>Bản tin sáng</h2><p>Bạn sẽ nhận nội dung như thế này</p></div><CalendarClock size={17} color="#7f8b9b" /></div>
            {!digest ? <div className={styles.empty}><Loader2 size={18} className={styles.spin} /></div> : (
              <ul className={styles.digestLines}>{digest.lines.map((line) => <li key={line}>{line}</li>)}</ul>
            )}
          </section>

          {schedule && (
            <section className={styles.panel}>
              <div className={styles.panelHeader}><div><h2>Lịch gửi</h2><p>Theo giờ của công ty</p></div></div>
              <div className={styles.scheduleGrid}>
                <div className={styles.toggleRow}>
                  <span className={styles.switch}><input id="ceo-alerts-on" type="checkbox" checked={schedule.alerts_enabled} disabled={!canManage} onChange={(event) => setSchedule({ ...schedule, alerts_enabled: event.target.checked })} /><span /></span>
                  <label htmlFor="ceo-alerts-on">Gửi cảnh báo vào thông báo (kiểm tra 2 giờ một lần)</label>
                </div>
                <div className={styles.toggleRow}>
                  <span className={styles.switch}><input id="ceo-digest-on" type="checkbox" checked={schedule.digest_enabled} disabled={!canManage} onChange={(event) => setSchedule({ ...schedule, digest_enabled: event.target.checked })} /><span /></span>
                  <span className={styles.inline}>
                    <label htmlFor="ceo-digest-on">Gửi bản tin lúc</label>
                    <select value={schedule.digest_hour} disabled={!canManage || !schedule.digest_enabled} onChange={(event) => setSchedule({ ...schedule, digest_hour: Number(event.target.value) })} aria-label="Giờ gửi bản tin">
                      {Array.from({ length: 24 }, (_, hour) => <option key={hour} value={hour}>{String(hour).padStart(2, "0")}:00</option>)}
                    </select>
                  </span>
                </div>
                <div className={styles.segmented} role="group" aria-label="Ngày gửi bản tin">
                  {WEEKDAYS.map((item) => (
                    <button key={item.day} type="button" aria-pressed={schedule.digest_weekdays.includes(item.day)} disabled={!canManage || !schedule.digest_enabled} onClick={() => toggleDay(item.day)}>{item.label}</button>
                  ))}
                </div>
                <p className={styles.muted}>
                  {schedule.last_digest_on ? `Bản tin gần nhất: ${viDate(schedule.last_digest_on)}.` : "Chưa gửi bản tin nào."}
                  {" "}Lịch do worker nền thực hiện; nếu worker không chạy thì không có gì được gửi.
                </p>
              </div>
            </section>
          )}
        </div>
      </div>
    </>
  );
}

/* --------------------------------------------------------------------------- chat view */

function ChatView({ visible, onError }: { visible: boolean; onError: (text: string) => void }) {
  const router = useRouter();
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState<ExecutionPhase | null>(null);
  const [streamed, setStreamed] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const askRef = useRef<(text: string) => Promise<void>>(async () => undefined);

  useEffect(() => {
    let active = true;
    api.get<Conversation[]>("/api/v1/agent/conversations")
      .then(async ({ data }) => {
        const latest = data.find((item) => item.agent_role === "CEO");
        if (!latest || !active) return;
        const detail = await api.get<{ messages: Message[] }>(`/api/v1/agent/conversations/${latest.id}`);
        if (!active) return;
        setConversationId(latest.id);
        setMessages(detail.data.messages);
      })
      .catch((reason) => onError(apiError(reason).message));
    return () => { active = false; };
  }, [onError]);

  useEffect(() => {
    if (visible) endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, streamed, phase, visible]);

  const ask = async (text: string) => {
    const content = text.trim();
    if (!content || busy) return;
    setQuestion("");
    setBusy(true);
    setPhase("ANALYZING");
    setStreamed("");
    setMessages((current) => [...current, { id: `pending-${Date.now()}`, sender: "USER", content }]);
    try {
      let finished: { conversation_id?: string } | null = null;
      await streamAgentChat({ agent_role: "CEO", message: content, conversation_id: conversationId }, (event) => {
        if (event.event === "status") setPhase(event.phase);
        else if (event.event === "token") setStreamed((current) => current + event.delta);
        else if (event.event === "complete") finished = event as { conversation_id?: string };
        else if (event.event === "error") throw new Error(event.message);
      });
      const id = (finished as { conversation_id?: string } | null)?.conversation_id;
      if (!id) throw new Error("Phản hồi chưa hoàn chỉnh, vui lòng thử lại.");
      const detail = await api.get<{ messages: Message[] }>(`/api/v1/agent/conversations/${id}`);
      setConversationId(id);
      setMessages(detail.data.messages);
    } catch (reason) {
      setMessages((current) => current.filter((item) => !item.id.startsWith("pending-")));
      setQuestion(content);
      onError(reason instanceof Error ? reason.message : apiError(reason).message);
    } finally {
      setBusy(false);
      setPhase(null);
      setStreamed("");
      inputRef.current?.focus();
    }
  };
  useEffect(() => {
    askRef.current = ask;
  });

  // "Hỏi nhanh" on the overview sends its question here.
  useEffect(() => {
    const onAsk = (event: Event) => void askRef.current(String((event as CustomEvent<string>).detail || ""));
    window.addEventListener("ceo-ask", onAsk);
    return () => window.removeEventListener("ceo-ask", onAsk);
  }, []);

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void ask(question);
    }
  };

  if (!visible) return null;
  return (
    <section className={styles.chatWorkspace}>
      <header className={styles.chatHeader}>
        <div>
          <span className={`${styles.statIcon} ${styles.blue}`}><Bot size={19} /></span>
          <div><strong>Chat với Trợ lý CEO</strong><small>Số liệu lấy từ dữ liệu các phòng, theo đúng quyền xem của bạn</small></div>
        </div>
        <button type="button" onClick={() => { setMessages([]); setConversationId(null); setQuestion(""); inputRef.current?.focus(); }}>
          <MessageSquareText size={14} />Cuộc trò chuyện mới
        </button>
      </header>
      <div className={styles.chatMessages}>
        {messages.length === 0 && !busy && (
          <div className={styles.chatEmpty}>
            <span><BriefcaseBusiness size={28} /></span>
            <h2>Bạn muốn nắm điều gì?</h2>
            <p>Hỏi về tình hình công ty, việc chờ duyệt, số liệu các phòng, hoặc nhờ giao việc cho một đồng nghiệp.</p>
            <div className={styles.askRow} style={{ justifyContent: "center" }}>
              {SUGGESTIONS.map((text) => <button key={text} onClick={() => void ask(text)}>{text}</button>)}
            </div>
          </div>
        )}
        {messages.map((message) => {
          const mine = message.sender === "USER";
          const tools = Array.from(new Set((message.tools_executed ?? []).map((item) => item.tool_name || "").filter((name) => TOOL_LABELS[name])));
          return (
            <article key={message.id} className={`${styles.message} ${mine ? styles.userMessage : ""}`}>
              <span>{mine ? "Bạn" : <Bot size={15} />}</span>
              <div className={styles.bubble}>
                {mine ? <div>{message.content}</div> : <ChatMessageContent content={message.content} />}
                {!mine && tools.length > 0 && <div className={styles.toolTrail} aria-label="Nguồn số liệu">{tools.map((name) => <span key={name}>{TOOL_LABELS[name]}</span>)}</div>}
                {!mine && tools.includes("list_my_approvals") && (
                  <div className={styles.bubbleActions}><button type="button" onClick={() => router.push("/approvals")}><ShieldCheck size={13} />Mở trang Phê duyệt</button></div>
                )}
              </div>
            </article>
          );
        })}
        {busy && (
          <article className={styles.message}>
            <span><Bot size={15} /></span>
            <div className={styles.bubble}>
              {streamed ? <div className={styles.streaming}>{streamed}</div> : <div className={styles.phase}><Loader2 size={14} className={styles.spin} />{phase ? PHASES[phase] : "Đang xử lý"}…</div>}
            </div>
          </article>
        )}
        <div ref={endRef} />
      </div>
      <div>
        <form className={styles.composer} onSubmit={(event) => { event.preventDefault(); void ask(question); }}>
          <textarea ref={inputRef} rows={1} value={question} onChange={(event) => setQuestion(event.target.value)} onKeyDown={onKeyDown}
            placeholder="Ví dụ: Lợi nhuận quý này so với quý trước? Giao chị Lan chuẩn bị báo cáo công nợ, hạn thứ 6…" aria-label="Câu hỏi cho Trợ lý CEO" />
          <button type="submit" disabled={busy || !question.trim()} aria-label="Gửi">{busy ? <Loader2 size={17} className={styles.spin} /> : <Send size={17} />}</button>
        </form>
        <div className={styles.composerHint}>Enter để gửi · Shift + Enter để xuống dòng</div>
      </div>
    </section>
  );
}
