import {
  AlertOctagon,
  AlertTriangle,
  Bell,
  CalendarClock,
  CheckCircle2,
  ClipboardCheck,
  Clock,
  FileCheck2,
  FileClock,
  Hourglass,
  ListChecks,
  Unplug,
  UserPlus,
  Wallet,
  type LucideIcon,
} from "lucide-react";

/** One notification as the API returns it. */
export interface NotificationItem {
  id: string;
  event_type: string;
  title: string;
  message: string;
  severity: string;
  entity_type?: string | null;
  entity_id?: string | null;
  is_read: boolean;
  created_at: string;
}

export type Tone = "blue" | "green" | "amber" | "red" | "gray";

/** Icon background and colour per tone; red/amber/green carry meaning, so the label says it too. */
export const TONES: Record<Tone, { bg: string; fg: string }> = {
  blue: { bg: "#E8F0FD", fg: "#2A6BC8" },
  green: { bg: "#E3F5EC", fg: "#16794C" },
  amber: { bg: "#FDF1DC", fg: "#A35C00" },
  red: { bg: "#FDE8E8", fg: "#B42318" },
  gray: { bg: "#F1F5F9", fg: "#64748B" },
};

interface EventMeta { label: string; icon: LucideIcon; tone: Tone }

const EVENTS: Record<string, EventMeta> = {
  APPROVAL_REQUIRED: { label: "Cần phê duyệt", icon: ClipboardCheck, tone: "amber" },
  APPROVAL_DECIDED: { label: "Có kết quả duyệt", icon: CheckCircle2, tone: "green" },
  LEAVE_APPROVAL_REQUIRED: { label: "Đơn nghỉ cần duyệt", icon: CalendarClock, tone: "amber" },
  LEAVE: { label: "Nghỉ phép", icon: CalendarClock, tone: "blue" },
  TASK_COMPLETED: { label: "Task hoàn thành", icon: ListChecks, tone: "green" },
  TASK_FAILED: { label: "Task gặp lỗi", icon: AlertTriangle, tone: "red" },
  TASK_DUE_SOON: { label: "Task sắp đến hạn", icon: Clock, tone: "amber" },
  WORKFLOW_FAILED: { label: "Quy trình gặp lỗi", icon: AlertOctagon, tone: "red" },
  AGENT_COST_LIMIT: { label: "Chạm ngưỡng chi phí AI", icon: Wallet, tone: "red" },
  DOCUMENT_READY: { label: "Tài liệu đã sẵn sàng", icon: FileCheck2, tone: "blue" },
  INTEGRATION_DISCONNECTED: { label: "Mất kết nối tích hợp", icon: Unplug, tone: "red" },
  ONBOARDING_TASK_CREATED: { label: "Việc hội nhập mới", icon: UserPlus, tone: "blue" },
  CONTRACT_EXPIRING: { label: "Hợp đồng sắp hết hạn", icon: FileClock, tone: "amber" },
  PROBATION_ENDING: { label: "Sắp hết thử việc", icon: Hourglass, tone: "amber" },
};

/** Label, icon and tone of a notification; an error or warning wins over the type's tone. */
export function eventMeta(item: Pick<NotificationItem, "event_type" | "severity">): EventMeta {
  const known = EVENTS[item.event_type] ?? {
    label: item.event_type.replaceAll("_", " ").toLowerCase(),
    icon: Bell,
    tone: "gray" as Tone,
  };
  if (item.severity === "ERROR" || item.severity === "CRITICAL") return { ...known, tone: "red" };
  if (item.severity === "WARNING" && known.tone !== "red") return { ...known, tone: "amber" };
  return known;
}

/** The label of an event type, for the preferences list. */
export function eventLabel(eventType: string): string {
  return EVENTS[eventType]?.label ?? eventType.replaceAll("_", " ").toLowerCase();
}

export const CHANNEL_LABELS: Record<string, string> = {
  IN_APP: "Trong ứng dụng",
  EMAIL: "Email",
  SLACK: "Slack",
  TEAMS: "Microsoft Teams",
  MOBILE_PUSH: "Thông báo trên điện thoại",
};

/** Where clicking a notification takes the reader, or null when there is nothing to open. */
export function notificationHref(item: NotificationItem): string | null {
  switch (item.entity_type) {
    case "APPROVAL":
      return item.entity_id ? `/approvals?id=${encodeURIComponent(item.entity_id)}` : "/approvals";
    case "LEAVE_REQUEST":
      return "/approvals";
    case "TASK":
      return "/tasks";
    case "WORKFLOW":
      return "/workflows";
    case "DOCUMENT":
      return "/knowledge";
    case "INTEGRATION":
      return "/integrations";
    case "BUDGET":
      return "/costs";
    case "EMPLOYMENT_CONTRACT":
      return "/users-mgmt";
    case "WORKSPACE":
      return "/settings";
    default:
      return null;
  }
}

function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/** "Vừa xong", "5 phút trước", "Hôm qua 14:20", "03/10/2026 09:15". */
export function relativeTime(iso: string, now: Date = new Date()): string {
  const at = new Date(iso);
  const minutes = Math.floor((now.getTime() - at.getTime()) / 60000);
  const clock = at.toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" });
  if (minutes < 1) return "Vừa xong";
  if (minutes < 60) return `${minutes} phút trước`;
  if (sameDay(at, now)) return `${Math.floor(minutes / 60)} giờ trước`;
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (sameDay(at, yesterday)) return `Hôm qua ${clock}`;
  return `${at.toLocaleDateString("vi-VN")} ${clock}`;
}

/** The notifications under "Hôm nay", "Hôm qua", "Trước đó", in that order, empty groups left out. */
export function groupByDay<T extends { created_at: string }>(items: T[], now: Date = new Date()): [string, T[]][] {
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  const groups: [string, T[]][] = [["Hôm nay", []], ["Hôm qua", []], ["Trước đó", []]];
  for (const item of items) {
    const at = new Date(item.created_at);
    groups[sameDay(at, now) ? 0 : sameDay(at, yesterday) ? 1 : 2][1].push(item);
  }
  return groups.filter(([, values]) => values.length > 0);
}
