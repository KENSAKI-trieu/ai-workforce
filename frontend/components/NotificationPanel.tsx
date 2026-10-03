"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { CheckCheck, Settings2, X } from "lucide-react";

import api from "@/lib/api";
import NotificationList from "@/components/NotificationList";
import { notificationHref, type NotificationItem } from "@/lib/notifications";
import { useLanguageStore } from "@/store/useLanguageStore";

const NOTIFICATION_TEXT = {
  vi: {
    close: "Đóng thông báo",
    title: "Thông báo",
    unreadCount: (count: number) => count ? `${count} thông báo chưa đọc` : "Bạn đã đọc hết",
    all: "Tất cả",
    unread: "Chưa đọc",
    markAll: "Đọc tất cả",
    loading: "Đang tải thông báo...",
    emptyAll: "Chưa có thông báo nào.",
    emptyUnread: "Không còn thông báo chưa đọc.",
    viewAll: "Xem tất cả và cấu hình",
  },
  ja: {
    close: "通知を閉じる",
    title: "通知",
    unreadCount: (count: number) => count ? `未読の通知 ${count} 件` : "すべて既読です",
    all: "すべて",
    unread: "未読",
    markAll: "すべて既読にする",
    loading: "通知を読み込んでいます...",
    emptyAll: "通知はありません。",
    emptyUnread: "未読の通知はありません。",
    viewAll: "すべて表示・設定",
  },
} as const;

interface NotificationPanelProps {
  open: boolean;
  onClose: () => void;
  onUnreadChange: (count: number) => void;
}

/**
 * The bell's panel: beside the menu on a wide screen, the whole screen on a phone
 * (both from .notif-panel in globals.css).
 */
export default function NotificationPanel({ open, onClose, onUnreadChange }: NotificationPanelProps) {
  const router = useRouter();
  const locale = useLanguageStore((state) => state.locale);
  const text = NOTIFICATION_TEXT[locale];
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [filter, setFilter] = useState<"all" | "unread">("all");
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/api/v1/notifications", { params: { limit: 50 } });
      setItems(data.items);
      setUnreadCount(data.unread_count);
      onUnreadChange(data.unread_count);
    } finally {
      setLoading(false);
    }
  }, [onUnreadChange]);

  useEffect(() => {
    if (!open) return;
    const timer = window.setTimeout(() => void load(), 0);
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("keydown", onKey);
    };
  }, [load, open, onClose]);

  const visibleItems = useMemo(
    () => filter === "unread" ? items.filter((item) => !item.is_read) : items,
    [filter, items],
  );

  const setUnread = (next: number) => {
    setUnreadCount(next);
    onUnreadChange(next);
  };

  async function openItem(item: NotificationItem) {
    const href = notificationHref(item);
    if (!item.is_read) {
      setItems((current) => current.map((value) => value.id === item.id ? { ...value, is_read: true } : value));
      setUnread(Math.max(0, unreadCount - 1));
      try {
        await api.post(`/api/v1/notifications/${item.id}/read`);
      } catch {
        await load();
      }
    }
    if (href) {
      onClose();
      router.push(href);
    }
  }

  async function markAll() {
    const previous = items;
    setItems((current) => current.map((item) => ({ ...item, is_read: true })));
    setUnread(0);
    try {
      await api.post("/api/v1/notifications/read-all");
    } catch {
      setItems(previous);
      await load();
    }
  }

  async function remove(item: NotificationItem) {
    setItems((current) => current.filter((value) => value.id !== item.id));
    if (!item.is_read) setUnread(Math.max(0, unreadCount - 1));
    try {
      await api.delete(`/api/v1/notifications/${item.id}`);
    } catch {
      await load();
    }
  }

  if (!open) return null;

  return (
    <>
      <button aria-label={text.close} onClick={onClose} className="notif-backdrop" />
      <section className="notif-panel" role="dialog" aria-label={text.title}>
        <header className="notif-panel-header">
          <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
            <div>
              <strong style={{ fontSize: 17, color: "var(--text-dark)" }}>{text.title}</strong>
              <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>{text.unreadCount(unreadCount)}</div>
            </div>
            <button className="ta-btn ta-btn-ghost" onClick={onClose} style={{ padding: 7 }} aria-label={text.close}><X size={16} /></button>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 12 }}>
            <div className="notif-segment" role="tablist">
              <button role="tab" aria-selected={filter === "all"} className={filter === "all" ? "active" : ""} onClick={() => setFilter("all")}>{text.all}</button>
              <button role="tab" aria-selected={filter === "unread"} className={filter === "unread" ? "active" : ""} onClick={() => setFilter("unread")}>
                {text.unread}{unreadCount ? ` (${unreadCount})` : ""}
              </button>
            </div>
            {unreadCount > 0 && (
              <button className="notif-link" onClick={() => void markAll()} style={{ marginLeft: "auto" }}>
                <CheckCheck size={14} /> {text.markAll}
              </button>
            )}
          </div>
        </header>

        <div className="notif-panel-body">
          {loading && !items.length
            ? <div className="notif-empty">{text.loading}</div>
            : <NotificationList
                items={visibleItems}
                onOpen={(item) => void openItem(item)}
                onRemove={(item) => void remove(item)}
                empty={filter === "unread" ? text.emptyUnread : text.emptyAll}
              />}
        </div>

        <footer className="notif-panel-footer">
          <button className="notif-link" onClick={() => { onClose(); router.push("/notifications"); }}>
            <Settings2 size={14} /> {text.viewAll}
          </button>
        </footer>
      </section>
    </>
  );
}
