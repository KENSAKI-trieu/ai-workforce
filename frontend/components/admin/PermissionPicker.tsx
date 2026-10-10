"use client";

import { useMemo, useState } from "react";
import { Search, X } from "lucide-react";

export interface PermissionMeta {
  code: string;
  group: string;
  section: string;
  label: string;
  description: string;
  coming_soon?: boolean;
}

interface PermissionPickerProps {
  catalog: PermissionMeta[];
  /** What the position holds now, to mark boxes changed but not saved yet. */
  saved: ReadonlySet<string>;
  draft: ReadonlySet<string>;
  onChange: (next: Set<string>) => void;
  /** The editor's own permissions: nobody can grant what they do not hold. */
  myPermissions: readonly string[];
  canManage: boolean;
}

type Section = { name: string; items: PermissionMeta[] };
type Group = { name: string; sections: Section[]; items: PermissionMeta[] };

/** Lowercase, no Vietnamese tone marks, so "luong" finds "Xem lương". */
function fold(text: string) {
  return text
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/đ/g, "d")
    .replace(/Đ/g, "d")
    .toLowerCase();
}

/** Groups and sections in catalog order; the backend keeps each one contiguous. */
function buildGroups(items: PermissionMeta[]): Group[] {
  const groups: Group[] = [];
  for (const item of items) {
    let group = groups.find((entry) => entry.name === item.group);
    if (!group) {
      group = { name: item.group, sections: [], items: [] };
      groups.push(group);
    }
    group.items.push(item);
    const sectionName = item.section || "Khác";
    let section = group.sections.find((entry) => entry.name === sectionName);
    if (!section) {
      section = { name: sectionName, items: [] };
      group.sections.push(section);
    }
    section.items.push(item);
  }
  return groups;
}

export default function PermissionPicker({
  catalog,
  saved,
  draft,
  onChange,
  myPermissions,
  canManage,
}: PermissionPickerProps) {
  const [activeGroup, setActiveGroup] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [onlyGranted, setOnlyGranted] = useState(false);

  const allGroups = useMemo(() => buildGroups(catalog), [catalog]);
  const currentGroup =
    allGroups.find((group) => group.name === activeGroup) ?? allGroups[0] ?? null;

  const grantable = (code: string) => !canManage || myPermissions.includes(code);

  // Searching or filtering looks across every group at once; otherwise one tab is open.
  const filtering = query.trim() !== "" || onlyGranted;
  const visibleGroups = useMemo(() => {
    if (!filtering) return currentGroup ? [currentGroup] : [];
    const needle = fold(query.trim());
    const matches = catalog.filter((item) => {
      // A box just unticked stays in view, marked "Sẽ bỏ", until the change is saved.
      if (onlyGranted && !draft.has(item.code) && !saved.has(item.code)) return false;
      if (!needle) return true;
      return fold(
        `${item.label} ${item.description} ${item.code} ${item.section} ${item.group}`,
      ).includes(needle);
    });
    return buildGroups(matches);
  }, [catalog, currentGroup, draft, filtering, onlyGranted, query, saved]);

  const pending = useMemo(() => {
    let added = 0;
    let removed = 0;
    for (const code of draft) if (!saved.has(code)) added += 1;
    for (const code of saved) if (!draft.has(code)) removed += 1;
    return { added, removed };
  }, [draft, saved]);

  const toggle = (code: string) => {
    const next = new Set(draft);
    if (next.has(code)) next.delete(code);
    else next.add(code);
    onChange(next);
  };

  // Only boxes the editor may grant; the rest stay as they are.
  const setMany = (items: PermissionMeta[], on: boolean) => {
    const next = new Set(draft);
    for (const item of items) {
      if (!grantable(item.code)) continue;
      if (on) next.add(item.code);
      else next.delete(item.code);
    }
    onChange(next);
  };

  // A section of two boxes is ticked faster by hand than through one more button.
  const bulkButton = (items: PermissionMeta[], whole: boolean) => {
    const codes = items.filter((item) => grantable(item.code));
    if (!canManage || codes.length < (whole ? 2 : 3)) return null;
    const allOn = codes.every((item) => draft.has(item.code));
    const label = whole
      ? allOn ? "Bỏ cả nhóm" : "Chọn cả nhóm"
      : allOn ? "Bỏ cả mục" : "Chọn cả mục";
    return (
      <button
        type="button"
        className="ta-btn ta-btn-ghost"
        style={{ padding: "2px 10px", fontSize: 12, marginLeft: "auto" }}
        onClick={() => setMany(codes, !allOn)}
      >
        {label}
      </button>
    );
  };

  return (
    <div>
      {/* ── Search, filter and totals ── */}
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 10, marginBottom: 12 }}>
        <div style={{ position: "relative", flex: "1 1 220px", minWidth: 0 }}>
          <Search
            size={14}
            style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: "var(--text-muted)" }}
          />
          <input
            className="ta-input"
            value={query}
            placeholder="Tìm quyền, ví dụ: lương, hợp đồng, duyệt…"
            onChange={(event) => setQuery(event.target.value)}
            style={{ paddingLeft: 30, paddingRight: query ? 30 : undefined }}
          />
          {query && (
            <button
              type="button"
              aria-label="Xoá tìm kiếm"
              onClick={() => setQuery("")}
              style={{
                position: "absolute", right: 6, top: "50%", transform: "translateY(-50%)",
                border: "none", background: "transparent", cursor: "pointer", lineHeight: 0,
                color: "var(--text-muted)",
              }}
            >
              <X size={14} />
            </button>
          )}
        </div>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, cursor: "pointer" }}>
          <input type="checkbox" checked={onlyGranted} onChange={(event) => setOnlyGranted(event.target.checked)} />
          Chỉ hiện quyền đang cấp
        </label>
      </div>
      <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 12 }}>
        Đang cấp <strong>{draft.size}</strong>/{catalog.length} quyền.
        {pending.added + pending.removed > 0 ? (
          <span style={{ color: "#B45309" }}>
            {" "}Chưa lưu: {pending.added > 0 && `+${pending.added} quyền`}
            {pending.added > 0 && pending.removed > 0 && ", "}
            {pending.removed > 0 && `−${pending.removed} quyền`}. Bấm Lưu để áp dụng.
          </span>
        ) : (
          " Thay đổi chỉ có hiệu lực sau khi bấm Lưu."
        )}
      </div>

      {/* ── Group tabs ── */}
      <div
        role="tablist"
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 6,
          paddingBottom: 12,
          marginBottom: 14,
          borderBottom: "1px solid var(--border, #E2E8F0)",
          opacity: filtering ? 0.55 : 1,
        }}
      >
        {allGroups.map((group) => {
          const held = group.items.filter((item) => draft.has(item.code)).length;
          const active = !filtering && group.name === currentGroup?.name;
          return (
            <button
              key={group.name}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => {
                setActiveGroup(group.name);
                setQuery("");
                setOnlyGranted(false);
              }}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                padding: "5px 11px",
                borderRadius: 999,
                fontSize: 13,
                fontWeight: active ? 700 : 500,
                cursor: "pointer",
                border: active ? "1px solid var(--primary)" : "1px solid var(--border, #E2E8F0)",
                background: active ? "var(--sidebar-active-bg, #EEF2FF)" : "transparent",
                color: active ? "var(--primary)" : "inherit",
              }}
            >
              {group.name}
              <span
                style={{
                  fontSize: 11,
                  fontWeight: 600,
                  padding: "0 6px",
                  borderRadius: 999,
                  background: held ? "var(--primary)" : "var(--border, #E2E8F0)",
                  color: held ? "#fff" : "var(--text-muted)",
                }}
              >
                {held}/{group.items.length}
              </span>
            </button>
          );
        })}
      </div>

      {/* ── Boxes ── */}
      {visibleGroups.length === 0 && (
        <div style={{ color: "var(--text-muted)", fontSize: 13, padding: "8px 0" }}>
          {onlyGranted && !query.trim()
            ? "Chức vụ này chưa được cấp quyền nào."
            : "Không có quyền nào khớp với từ khoá."}
        </div>
      )}
      {visibleGroups.map((group) => (
        <div key={group.name} style={{ marginBottom: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
            <strong style={{ fontSize: 14 }}>{group.name}</strong>
            {!filtering && bulkButton(group.items, true)}
          </div>
          {group.sections.map((section) => (
            <div key={section.name} style={{ marginBottom: 12 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                <span
                  style={{
                    fontSize: 11,
                    fontWeight: 700,
                    color: "var(--text-muted)",
                    textTransform: "uppercase",
                    letterSpacing: "0.05em",
                  }}
                >
                  {section.name}
                </span>
                {!filtering && group.sections.length > 1 && bulkButton(section.items, false)}
              </div>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))",
                  gap: 6,
                }}
              >
                {section.items.map((permission) => {
                  const checked = draft.has(permission.code);
                  const allowed = grantable(permission.code);
                  const changed = checked !== saved.has(permission.code);
                  return (
                    <label
                      key={permission.code}
                      title={
                        allowed
                          ? permission.code
                          : "Bạn không có quyền này nên không thể cấp cho người khác."
                      }
                      style={{
                        display: "flex",
                        gap: 8,
                        alignItems: "flex-start",
                        padding: "7px 9px",
                        borderRadius: 8,
                        border: checked
                          ? "1px solid var(--primary)"
                          : "1px solid var(--border, #E2E8F0)",
                        background: checked ? "var(--sidebar-active-bg, #EEF2FF)" : "transparent",
                        opacity: allowed ? 1 : 0.5,
                        cursor: canManage && allowed ? "pointer" : "not-allowed",
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        disabled={!canManage || !allowed}
                        onChange={() => toggle(permission.code)}
                        style={{ marginTop: 3 }}
                      />
                      <span style={{ minWidth: 0 }}>
                        <span style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 6 }}>
                          <strong style={{ fontSize: 13 }}>{permission.label}</strong>
                          {permission.coming_soon && (
                            <span
                              className="ta-badge ta-badge-neutral"
                              style={{ fontSize: 10 }}
                              title="Cấp trước được, nhưng tính năng dùng quyền này chưa mở."
                            >
                              Sắp có
                            </span>
                          )}
                          {changed && (
                            <span className="ta-badge ta-badge-warning" style={{ fontSize: 10 }}>
                              {checked ? "Mới cấp" : "Sẽ bỏ"}
                            </span>
                          )}
                        </span>
                        <span style={{ display: "block", fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>
                          {permission.description}
                        </span>
                      </span>
                    </label>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
