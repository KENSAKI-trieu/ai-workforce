"use client";

import { ChevronRight, Trash2 } from "lucide-react";

import {
  TONES,
  eventMeta,
  groupByDay,
  notificationHref,
  relativeTime,
  type NotificationItem,
} from "@/lib/notifications";

interface NotificationListProps {
  items: NotificationItem[];
  /** Marks the notification read; the list follows its link afterwards, if it has one. */
  onOpen: (item: NotificationItem) => void;
  onRemove?: (item: NotificationItem) => void;
  empty: string;
}

/** Notifications under day headings, each with the icon and label of its kind. */
export default function NotificationList({ items, onOpen, onRemove, empty }: NotificationListProps) {
  if (!items.length) return <div className="notif-empty">{empty}</div>;
  return (
    <div className="notif-list">
      {groupByDay(items).map(([day, values]) => (
        <section key={day}>
          <h3 className="notif-day">{day}</h3>
          {values.map((item) => {
            const meta = eventMeta(item);
            const tone = TONES[meta.tone];
            const href = notificationHref(item);
            const Icon = meta.icon;
            return (
              <div key={item.id} className={`notif-row${item.is_read ? "" : " unread"}`}>
                <button
                  type="button"
                  className="notif-main"
                  onClick={() => onOpen(item)}
                  aria-label={`${item.title}${href ? " — mở" : ""}`}
                >
                  <span className="notif-icon" style={{ background: tone.bg, color: tone.fg }} aria-hidden>
                    <Icon size={17} />
                  </span>
                  <span className="notif-body">
                    <span className="notif-title">{item.title}</span>
                    <span className="notif-message">{item.message}</span>
                    <span className="notif-meta">
                      <span style={{ color: tone.fg, fontWeight: 600 }}>{meta.label}</span>
                      <span aria-hidden>·</span>
                      <time dateTime={item.created_at} title={new Date(item.created_at).toLocaleString("vi-VN")}>
                        {relativeTime(item.created_at)}
                      </time>
                    </span>
                  </span>
                  {!item.is_read && <span className="notif-dot" title="Chưa đọc" />}
                  {href && <ChevronRight size={16} className="notif-chevron" aria-hidden />}
                </button>
                {onRemove && (
                  <button
                    type="button"
                    className="notif-remove"
                    onClick={() => onRemove(item)}
                    aria-label="Xoá thông báo"
                    title="Xoá"
                  >
                    <Trash2 size={14} />
                  </button>
                )}
              </div>
            );
          })}
        </section>
      ))}
    </div>
  );
}
