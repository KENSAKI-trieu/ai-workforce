"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Bell, BellRing, CheckCheck, Trash2, X } from "lucide-react";

import api from "@/lib/api";
import { useLanguageStore } from "@/store/useLanguageStore";

const NOTIFICATION_TEXT = {
  vi: {
    close: "Đóng thông báo",
    title: "Thông báo",
    unreadCount: (count: number) => `${count} thông báo chưa đọc`,
    all: "Tất cả",
    unread: "Chưa đọc",
    markAll: "Đọc tất cả",
    loading: "Đang tải thông báo...",
    emptyAll: "Không có thông báo nào.",
    emptyUnread: "Không có thông báo chưa đọc.",
    read: "Đã đọc",
    markRead: "Đánh dấu đã đọc",
    deleteNotification: "Xóa thông báo",
    delete: "Xóa",
    dateLocale: "vi-VN",
  },
  ja: {
    close: "通知を閉じる",
    title: "通知",
    unreadCount: (count: number) => `未読の通知 ${count} 件`,
    all: "すべて",
    unread: "未読",
    markAll: "すべて既読にする",
    loading: "通知を読み込んでいます...",
    emptyAll: "通知はありません。",
    emptyUnread: "未読の通知はありません。",
    read: "既読",
    markRead: "既読にする",
    deleteNotification: "通知を削除",
    delete: "削除",
    dateLocale: "ja-JP",
  },
} as const;

interface NotificationItem {
  id: string;
  event_type: string;
  title: string;
  message: string;
  severity: string;
  is_read: boolean;
  created_at: string;
}

interface NotificationPanelProps {
  open: boolean;
  onClose: () => void;
  onUnreadChange: (count: number) => void;
}

export default function NotificationPanel({ open, onClose, onUnreadChange }: NotificationPanelProps) {
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
    return () => window.clearTimeout(timer);
  }, [load, open]);

  const visibleItems = useMemo(
    () => filter === "unread" ? items.filter((item) => !item.is_read) : items,
    [filter, items],
  );

  async function markRead(item: NotificationItem) {
    if (item.is_read) return;
    setItems((current) => current.map((value) => value.id === item.id ? { ...value, is_read: true } : value));
    setUnreadCount((current) => {
      const next = Math.max(0, current - 1);
      onUnreadChange(next);
      return next;
    });
    try {
      await api.post(`/api/v1/notifications/${item.id}/read`);
    } catch {
      await load();
    }
  }

  async function markAll() {
    const previous = items;
    setItems((current) => current.map((item) => ({ ...item, is_read: true })));
    setUnreadCount(0);
    onUnreadChange(0);
    try {
      await api.post("/api/v1/notifications/read-all");
    } catch {
      setItems(previous);
      await load();
    }
  }

  async function remove(item: NotificationItem) {
    setItems((current) => current.filter((value) => value.id !== item.id));
    if (!item.is_read) {
      setUnreadCount((current) => {
        const next = Math.max(0, current - 1);
        onUnreadChange(next);
        return next;
      });
    }
    try {
      await api.delete(`/api/v1/notifications/${item.id}`);
    } catch {
      await load();
    }
  }

  if (!open) return null;

  return (
    <>
      <button
        aria-label={text.close}
        onClick={onClose}
        style={{ position: "fixed", inset: 0, border: 0, background: "rgba(15,23,42,.08)", zIndex: 59 }}
      />
      <section style={{
        position: "fixed", left: 282, top: 12, bottom: 12, width: 390, zIndex: 60,
        background: "#fff", border: "1px solid #E2E8F0", borderRadius: 16,
        boxShadow: "0 24px 70px rgba(15,23,42,.22)", display: "flex", flexDirection: "column", overflow: "hidden",
      }}>
        <header style={{ padding: "18px 18px 12px", borderBottom: "1px solid #EEF2F7" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <div>
              <strong style={{ fontSize: 17, color: "#172033" }}>{text.title}</strong>
              <div style={{ fontSize: 12, color: "#64748B", marginTop: 2 }}>{text.unreadCount(unreadCount)}</div>
            </div>
            <button className="ta-btn ta-btn-ghost" onClick={onClose} style={{ padding: 7 }}><X size={16}/></button>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 13 }}>
            <button className={`ta-btn ${filter === "all" ? "ta-btn-primary" : "ta-btn-ghost"}`} onClick={() => setFilter("all")}>{text.all}</button>
            <button className={`ta-btn ${filter === "unread" ? "ta-btn-primary" : "ta-btn-ghost"}`} onClick={() => setFilter("unread")}>{text.unread}</button>
            {unreadCount > 0 && <button className="ta-btn ta-btn-ghost" onClick={() => void markAll()} style={{ marginLeft: "auto" }}><CheckCheck size={14}/> {text.markAll}</button>}
          </div>
        </header>

        <div style={{ flex: 1, overflowY: "auto" }}>
          {loading && !items.length && <div style={{ padding: 24, color: "#64748B", textAlign: "center" }}>{text.loading}</div>}
          {!loading && !visibleItems.length && <div style={{ padding: 32, color: "#64748B", textAlign: "center" }}>{filter === "unread" ? text.emptyUnread : text.emptyAll}</div>}
          {visibleItems.map((item) => (
            <div key={item.id} style={{
              display: "flex", gap: 11, padding: "14px 15px", borderBottom: "1px solid #F1F5F9",
              background: item.is_read ? "#fff" : "#F5F7FF",
            }}>
              <button onClick={() => void markRead(item)} aria-label={item.is_read ? text.read : text.markRead} style={{
                width: 34, height: 34, borderRadius: 10, flexShrink: 0, border: 0,
                background: item.is_read ? "#F1F5F9" : "#E0E7FF", color: item.is_read ? "#64748B" : "#4F46E5", cursor: item.is_read ? "default" : "pointer",
              }}>
                {item.is_read ? <Bell size={16}/> : <BellRing size={16}/>} 
              </button>
              <button onClick={() => void markRead(item)} style={{ flex: 1, border: 0, background: "transparent", textAlign: "left", padding: 0, cursor: item.is_read ? "default" : "pointer" }}>
                <strong style={{ display: "block", fontSize: 13, color: "#1E293B" }}>{item.title}</strong>
                <span style={{ display: "block", fontSize: 12, color: "#64748B", marginTop: 4, lineHeight: 1.45 }}>{item.message}</span>
                <span style={{ display: "block", fontSize: 10, color: "#94A3B8", marginTop: 6 }}>{new Date(item.created_at).toLocaleString(text.dateLocale)}</span>
              </button>
              <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8 }}>
                {!item.is_read && (
                  <span title={text.unread} style={{ width: 7, height: 7, borderRadius: "50%", background: "#4F46E5" }}/>
                )}
                <button onClick={() => void remove(item)} aria-label={text.deleteNotification} title={text.delete} style={{ border: 0, background: "transparent", color: "#94A3B8", cursor: "pointer", padding: 3 }}><Trash2 size={14}/></button>
              </div>
            </div>
          ))}
        </div>
      </section>
    </>
  );
}
