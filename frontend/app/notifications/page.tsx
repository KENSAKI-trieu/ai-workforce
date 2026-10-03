"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Bell, CheckCheck, RefreshCw, Settings2 } from "lucide-react";

import AdminShell from "@/components/admin/AdminShell";
import NotificationList from "@/components/NotificationList";
import api from "@/lib/api";
import { CHANNEL_LABELS, eventLabel, notificationHref, type NotificationItem } from "@/lib/notifications";

interface Preferences {
  event_catalog: string[];
  channel_catalog: string[];
  enabled_event_types: string[];
  enabled_channels: string[];
  quiet_hours: { enabled?: boolean; start?: string; end?: string; timezone?: string };
}

export default function NotificationsPage() {
  const router = useRouter();
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [filter, setFilter] = useState<"all" | "unread">("all");
  const [unread, setUnread] = useState(0);
  const [preferences, setPreferences] = useState<Preferences | null>(null);
  const [tab, setTab] = useState<"inbox" | "preferences">("inbox");
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    const [notifications, prefs] = await Promise.all([
      api.get("/api/v1/notifications"),
      api.get("/api/v1/notifications/preferences"),
    ]);
    setItems(notifications.data.items);
    setUnread(notifications.data.unread_count);
    setPreferences(prefs.data);
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  async function scan() {
    const response = await api.post("/api/v1/notifications/scan");
    setMessage(`Đã tạo ${response.data.created} thông báo mới, các sự kiện trùng đã được bỏ qua.`);
    await load();
  }

  async function openItem(item: NotificationItem) {
    if (!item.is_read) {
      await api.post(`/api/v1/notifications/${item.id}/read`);
      await load();
    }
    const href = notificationHref(item);
    if (href) router.push(href);
  }

  async function remove(item: NotificationItem) {
    await api.delete(`/api/v1/notifications/${item.id}`);
    await load();
  }

  const visible = useMemo(() => filter === "unread" ? items.filter((item) => !item.is_read) : items, [filter, items]);

  async function markAll() {
    await api.post("/api/v1/notifications/read-all");
    await load();
  }

  function togglePreference(field: "enabled_event_types" | "enabled_channels", value: string) {
    if (!preferences) return;
    const current = preferences[field];
    setPreferences({
      ...preferences,
      [field]: current.includes(value)
        ? current.filter((item) => item !== value)
        : [...current, value],
    });
  }

  async function savePreferences() {
    if (!preferences) return;
    await api.put("/api/v1/notifications/preferences", {
      enabled_event_types: preferences.enabled_event_types,
      enabled_channels: preferences.enabled_channels,
      quiet_hours: {
        enabled: preferences.quiet_hours.enabled || false,
        start: preferences.quiet_hours.start || "22:00",
        end: preferences.quiet_hours.end || "07:00",
        timezone: preferences.quiet_hours.timezone || "Asia/Ho_Chi_Minh",
      },
    });
    setMessage("Đã lưu cấu hình thông báo.");
  }

  return (
    <AdminShell
      title="Thông báo"
      description="Theo dõi task, workflow, phê duyệt, chi phí, tài liệu và trạng thái integration."
      action={<button className="ops-button secondary" onClick={() => void scan()}><RefreshCw size={15}/>Kiểm tra sự kiện mới</button>}
    >
      {message && <div className="ops-alert">{message}</div>}
      <div className="ops-toolbar" style={{ marginBottom: 16 }}>
        <button className={`ops-button ${tab === "inbox" ? "" : "secondary"}`} onClick={() => setTab("inbox")}>
          <Bell size={15}/> Hộp thư
        </button>
        <button className={`ops-button ${tab === "preferences" ? "" : "secondary"}`} onClick={() => setTab("preferences")}>
          <Settings2 size={15}/> Cấu hình
        </button>
        {tab === "inbox" && unread > 0 && (
          <button className="ops-button secondary" onClick={() => void markAll()}><CheckCheck size={15}/>Đánh dấu đã đọc</button>
        )}
      </div>

      {tab === "inbox" && (
        <div className="ops-card" style={{ padding: 0, overflow: "hidden" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "12px 14px", borderBottom: "1px solid #eef2f7" }}>
            <div className="notif-segment" role="tablist">
              <button role="tab" aria-selected={filter === "all"} className={filter === "all" ? "active" : ""} onClick={() => setFilter("all")}>Tất cả ({items.length})</button>
              <button role="tab" aria-selected={filter === "unread"} className={filter === "unread" ? "active" : ""} onClick={() => setFilter("unread")}>Chưa đọc ({unread})</button>
            </div>
          </div>
          <NotificationList
            items={visible}
            onOpen={(item) => void openItem(item)}
            onRemove={(item) => void remove(item)}
            empty={filter === "unread" ? "Không còn thông báo chưa đọc." : "Chưa có thông báo. Bấm “Kiểm tra sự kiện mới” để quét."}
          />
        </div>
      )}

      {tab === "preferences" && preferences && (
        <div className="ops-grid ops-grid-2">
          <section className="ops-card">
            <div className="ops-card-header"><h2>Loại sự kiện</h2></div>
            {preferences.event_catalog.map((eventType) => (
              <label key={eventType} style={{ display: "flex", gap: 9, padding: "8px 0", fontSize: ".8rem" }}>
                <input type="checkbox" checked={preferences.enabled_event_types.includes(eventType)} onChange={() => togglePreference("enabled_event_types", eventType)} />
                {eventLabel(eventType)}
              </label>
            ))}
          </section>
          <section className="ops-card">
            <div className="ops-card-header"><h2>Kênh nhận</h2></div>
            {preferences.channel_catalog.map((channel) => (
              <label key={channel} style={{ display: "flex", justifyContent: "space-between", gap: 9, padding: "9px 0", fontSize: ".8rem" }}>
                <span><input type="checkbox" checked={preferences.enabled_channels.includes(channel)} onChange={() => togglePreference("enabled_channels", channel)} style={{ marginRight: 9 }} />{CHANNEL_LABELS[channel] ?? channel}</span>
                {channel !== "IN_APP" && <span className="ops-badge warning">Cần connector</span>}
              </label>
            ))}
            <hr style={{ border: 0, borderTop: "1px solid #eef2f7", margin: "15px 0" }}/>
            <div className="ops-form-grid">
              <div className="ops-field"><label>Bắt đầu yên lặng</label><input type="time" value={preferences.quiet_hours.start || "22:00"} onChange={(e) => setPreferences({...preferences, quiet_hours: {...preferences.quiet_hours, start: e.target.value}})} /></div>
              <div className="ops-field"><label>Kết thúc</label><input type="time" value={preferences.quiet_hours.end || "07:00"} onChange={(e) => setPreferences({...preferences, quiet_hours: {...preferences.quiet_hours, end: e.target.value}})} /></div>
            </div>
            <label style={{ display: "flex", gap: 9, margin: "14px 0", fontSize: ".8rem" }}>
              <input type="checkbox" checked={preferences.quiet_hours.enabled || false} onChange={(e) => setPreferences({...preferences, quiet_hours: {...preferences.quiet_hours, enabled: e.target.checked}})} />
              Không báo trong giờ yên lặng
            </label>
            <button className="ops-button" onClick={() => void savePreferences()}>Lưu cấu hình</button>
          </section>
        </div>
      )}
    </AdminShell>
  );
}
