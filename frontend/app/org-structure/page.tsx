"use client";

import axios from "axios";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ChevronDown,
  ChevronRight,
  Loader2,
  Plus,
  Save,
  Shield,
  Trash2,
  Users,
} from "lucide-react";

import Sidebar from "@/components/Sidebar";
import api from "@/lib/api";
import { useAuthStore } from "@/store/useAuthStore";

interface Position {
  id: string;
  parent_id: string | null;
  name: string;
  slug: string;
  permissions: string[];
  grants_all: boolean;
  default_department: string | null;
  is_active: boolean;
  sort_order: number;
  holder_count: number;
}

interface PermissionMeta {
  code: string;
  group: string;
  label: string;
  description: string;
}

interface Holder {
  id: string;
  full_name: string;
  email: string;
  department: string;
  is_active: boolean;
}

interface TreeResponse {
  positions: Position[];
  my_permissions: string[];
}

function messageFrom(error: unknown) {
  return axios.isAxiosError(error)
    ? String(error.response?.data?.detail || error.message)
    : "Không thể xử lý yêu cầu.";
}

/** Nested rows built from parent_id, so no tree library is needed. */
function orderTree(positions: Position[]): Array<{ node: Position; depth: number }> {
  const childrenOf = new Map<string | null, Position[]>();
  for (const position of positions) {
    const bucket = childrenOf.get(position.parent_id) ?? [];
    bucket.push(position);
    childrenOf.set(position.parent_id, bucket);
  }
  for (const bucket of childrenOf.values()) {
    bucket.sort((a, b) => a.sort_order - b.sort_order || a.name.localeCompare(b.name));
  }
  const known = new Set(positions.map((item) => item.id));
  const rows: Array<{ node: Position; depth: number }> = [];
  const seen = new Set<string>();

  const walk = (parentId: string | null, depth: number) => {
    for (const node of childrenOf.get(parentId) ?? []) {
      if (seen.has(node.id)) continue;
      seen.add(node.id);
      rows.push({ node, depth });
      walk(node.id, depth + 1);
    }
  };
  walk(null, 0);
  // A node whose parent was filtered out or deleted still has to be reachable.
  for (const position of positions) {
    if (!seen.has(position.id) && (position.parent_id === null || !known.has(position.parent_id))) {
      seen.add(position.id);
      rows.push({ node: position, depth: 0 });
      walk(position.id, 1);
    }
  }
  for (const position of positions) {
    if (!seen.has(position.id)) {
      seen.add(position.id);
      rows.push({ node: position, depth: 0 });
    }
  }
  return rows;
}

export default function OrgStructurePage() {
  const router = useRouter();
  const { isAuthenticated, hasHydrated } = useAuthStore();

  const [positions, setPositions] = useState<Position[]>([]);
  const [catalog, setCatalog] = useState<PermissionMeta[]>([]);
  const [myPermissions, setMyPermissions] = useState<string[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [holders, setHolders] = useState<Holder[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const [draftName, setDraftName] = useState("");
  const [draftParent, setDraftParent] = useState<string>("");
  const [draftPermissions, setDraftPermissions] = useState<Set<string>>(new Set());

  const [showCreate, setShowCreate] = useState(false);
  const [newPosition, setNewPosition] = useState({ name: "", parent_id: "" });

  const canManage = myPermissions.includes("org.structure.manage");
  const selected = useMemo(
    () => positions.find((item) => item.id === selectedId) ?? null,
    [positions, selectedId],
  );

  const loadTree = useCallback(async (keepSelection?: string | null) => {
    setLoading(true);
    setError(null);
    try {
      const [tree, permissions] = await Promise.all([
        api.get<TreeResponse>("/api/v1/positions"),
        api.get<{ permissions: PermissionMeta[] }>("/api/v1/positions/permissions"),
      ]);
      setPositions(tree.data.positions);
      setMyPermissions(tree.data.my_permissions);
      setCatalog(permissions.data.permissions);
      setSelectedId((current) => {
        const target = keepSelection ?? current;
        return tree.data.positions.some((item) => item.id === target)
          ? target
          : tree.data.positions[0]?.id ?? null;
      });
    } catch (reason) {
      setError(messageFrom(reason));
    } finally {
      setLoading(false);
    }
  }, []);

  // Deferred a tick, matching the other admin pages: the lint rule forbids calling
  // setState synchronously inside an effect body, which `loadTree` does first thing.
  useEffect(() => {
    if (!hasHydrated) return;
    if (!isAuthenticated) {
      router.replace("/login");
      return;
    }
    const timer = window.setTimeout(() => void loadTree(), 0);
    return () => window.clearTimeout(timer);
  }, [hasHydrated, isAuthenticated, loadTree, router]);

  // Reset the edit form whenever a different position is opened. Deferred for the same
  // reason; the cleanup also drops a holders reply for a position no longer open.
  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setDraftName(selected.name);
      setDraftParent(selected.parent_id ?? "");
      setDraftPermissions(new Set(selected.permissions));
      setHolders([]);
      void (async () => {
        try {
          const { data } = await api.get<{ holders: Holder[] }>(
            `/api/v1/positions/${selected.id}/holders`,
          );
          if (!cancelled) setHolders(data.holders);
        } catch {
          if (!cancelled) setHolders([]);
        }
      })();
    }, 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [selected]);

  const groupedCatalog = useMemo(() => {
    const groups = new Map<string, PermissionMeta[]>();
    for (const item of catalog) {
      const bucket = groups.get(item.group) ?? [];
      bucket.push(item);
      groups.set(item.group, bucket);
    }
    return Array.from(groups.entries());
  }, [catalog]);

  const rows = useMemo(() => {
    const hidden = new Set<string>();
    const ordered = orderTree(positions);
    return ordered.filter(({ node }) => {
      let parent = node.parent_id;
      while (parent) {
        if (collapsed.has(parent) || hidden.has(parent)) {
          hidden.add(node.id);
          return false;
        }
        parent = positions.find((item) => item.id === parent)?.parent_id ?? null;
      }
      return true;
    });
  }, [collapsed, positions]);

  const hasChildren = useCallback(
    (id: string) => positions.some((item) => item.parent_id === id),
    [positions],
  );

  const togglePermission = (code: string) => {
    setDraftPermissions((current) => {
      const next = new Set(current);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  };

  const dirty = useMemo(() => {
    if (!selected) return false;
    if (draftName.trim() !== selected.name) return true;
    if ((draftParent || null) !== selected.parent_id) return true;
    if (selected.grants_all) return false;
    const before = new Set(selected.permissions);
    if (before.size !== draftPermissions.size) return true;
    for (const code of draftPermissions) if (!before.has(code)) return true;
    return false;
  }, [draftName, draftParent, draftPermissions, selected]);

  const saveSelected = async () => {
    if (!selected) return;
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const payload: Record<string, unknown> = {};
      if (draftName.trim() !== selected.name) payload.name = draftName.trim();
      if ((draftParent || null) !== selected.parent_id) payload.parent_id = draftParent || null;
      if (!selected.grants_all) payload.permissions = Array.from(draftPermissions);
      await api.patch(`/api/v1/positions/${selected.id}`, payload);
      setNotice("Đã lưu thay đổi.");
      await loadTree(selected.id);
    } catch (reason) {
      setError(messageFrom(reason));
    } finally {
      setSaving(false);
    }
  };

  const createPosition = async () => {
    setSaving(true);
    setError(null);
    try {
      const { data } = await api.post<Position>("/api/v1/positions", {
        name: newPosition.name.trim(),
        parent_id: newPosition.parent_id || null,
        permissions: [],
      });
      setShowCreate(false);
      setNewPosition({ name: "", parent_id: "" });
      setNotice(`Đã tạo chức vụ "${data.name}".`);
      await loadTree(data.id);
    } catch (reason) {
      setError(messageFrom(reason));
    } finally {
      setSaving(false);
    }
  };

  const removeSelected = async () => {
    if (!selected) return;
    const replacement =
      selected.holder_count > 0
        ? window.prompt(
            `Chức vụ này còn ${selected.holder_count} người đang giữ.\n` +
              "Nhập tên chức vụ sẽ tiếp nhận họ:",
          )
        : null;
    if (selected.holder_count > 0 && !replacement) return;
    const target = replacement
      ? positions.find(
          (item) =>
            item.id !== selected.id &&
            item.name.trim().toLowerCase() === replacement.trim().toLowerCase(),
        )
      : null;
    if (replacement && !target) {
      setError(`Không tìm thấy chức vụ tên "${replacement}".`);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await api.delete(`/api/v1/positions/${selected.id}`, {
        params: target ? { reassign_to: target.id } : undefined,
      });
      setNotice("Đã xoá chức vụ.");
      await loadTree(null);
    } catch (reason) {
      setError(messageFrom(reason));
    } finally {
      setSaving(false);
    }
  };

  if (!hasHydrated || !isAuthenticated) return null;

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "var(--body-bg)" }}>
      <Sidebar />
      <div style={{ flex: 1, minWidth: 0 }}>
        <header className="ta-topbar">
          <div className="breadcrumb">
            <span>Home</span>
            <span className="breadcrumb-sep">›</span>
            <span className="breadcrumb-current">Cơ cấu tổ chức</span>
          </div>
          {canManage && (
            <button className="ta-btn ta-btn-primary" onClick={() => setShowCreate(true)}>
              <Plus size={15} /> Thêm chức vụ
            </button>
          )}
        </header>

        <main style={{ padding: "24px 32px" }}>
          <h1
            style={{
              display: "flex",
              alignItems: "center",
              gap: 9,
              fontSize: "1.5rem",
              fontWeight: 800,
            }}
          >
            <Shield size={24} color="var(--primary)" /> Cơ cấu chức vụ & phân quyền
          </h1>
          <p style={{ color: "var(--text-muted)", margin: "6px 0 18px" }}>
            Công ty tự đặt tên chức vụ và sắp xếp thứ bậc. Đổi tên không ảnh hưởng tới quyền
            đã cấp hay tài liệu đã phân quyền.
          </p>

          {error && (
            <div className="ta-card" style={{ color: "#B91C1C", padding: 13, marginBottom: 14 }}>
              {error}
            </div>
          )}
          {notice && (
            <div className="ta-card" style={{ color: "#15803D", padding: 13, marginBottom: 14 }}>
              {notice}
            </div>
          )}
          {!canManage && (
            <div className="ta-card" style={{ padding: 13, marginBottom: 14, color: "var(--text-muted)" }}>
              Bạn đang xem ở chế độ chỉ đọc. Cần quyền <strong>Quản lý cơ cấu chức vụ</strong> để
              chỉnh sửa.
            </div>
          )}

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "minmax(280px, 380px) minmax(0, 1fr)",
              gap: 16,
              alignItems: "start",
            }}
          >
            {/* ── Tree ── */}
            <div className="ta-card" style={{ padding: 12 }}>
              <div
                style={{
                  fontSize: 12,
                  fontWeight: 700,
                  color: "var(--text-muted)",
                  textTransform: "uppercase",
                  letterSpacing: "0.06em",
                  marginBottom: 8,
                }}
              >
                Cây chức vụ
              </div>
              {loading ? (
                <div style={{ padding: 16, color: "var(--text-muted)" }}>Đang tải…</div>
              ) : (
                <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
                  {rows.map(({ node, depth }) => {
                    const expandable = hasChildren(node.id);
                    const isCollapsed = collapsed.has(node.id);
                    const active = node.id === selectedId;
                    return (
                      <li key={node.id}>
                        <div
                          onClick={() => setSelectedId(node.id)}
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: 6,
                            padding: "8px 8px 8px 6px",
                            marginLeft: depth * 16,
                            borderRadius: 8,
                            cursor: "pointer",
                            background: active ? "var(--primary-soft, #EEF2FF)" : "transparent",
                            border: active
                              ? "1px solid var(--primary)"
                              : "1px solid transparent",
                          }}
                        >
                          <button
                            aria-label={isCollapsed ? "Mở rộng" : "Thu gọn"}
                            onClick={(event) => {
                              event.stopPropagation();
                              setCollapsed((current) => {
                                const next = new Set(current);
                                if (next.has(node.id)) next.delete(node.id);
                                else next.add(node.id);
                                return next;
                              });
                            }}
                            style={{
                              border: "none",
                              background: "transparent",
                              cursor: expandable ? "pointer" : "default",
                              opacity: expandable ? 1 : 0.25,
                              padding: 0,
                              lineHeight: 0,
                            }}
                            disabled={!expandable}
                          >
                            {isCollapsed ? <ChevronRight size={15} /> : <ChevronDown size={15} />}
                          </button>
                          <span style={{ fontWeight: active ? 700 : 500, flex: 1, minWidth: 0 }}>
                            {node.name}
                          </span>
                          {node.grants_all && (
                            <span className="ta-badge ta-badge-success" style={{ fontSize: 10 }}>
                              Toàn quyền
                            </span>
                          )}
                          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
                            {node.holder_count}
                          </span>
                        </div>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>

            {/* ── Detail ── */}
            <div className="ta-card" style={{ padding: 18 }}>
              {!selected ? (
                <div style={{ color: "var(--text-muted)" }}>
                  Chọn một chức vụ ở cây bên trái để xem chi tiết.
                </div>
              ) : (
                <>
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "flex-start",
                      gap: 12,
                      marginBottom: 14,
                    }}
                  >
                    <div style={{ minWidth: 0 }}>
                      <h2 style={{ fontSize: "1.15rem", fontWeight: 800, margin: 0 }}>
                        {selected.name}
                      </h2>
                      <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 4 }}>
                        Mã cố định: <code>{selected.slug}</code> · {selected.holder_count} người
                        đang giữ
                      </div>
                    </div>
                    <div style={{ display: "flex", gap: 8 }}>
                      {canManage && (
                        <button
                          className="ta-btn ta-btn-primary"
                          disabled={!dirty || saving}
                          onClick={() => void saveSelected()}
                        >
                          {saving ? <Loader2 size={15} className="spin" /> : <Save size={15} />} Lưu
                        </button>
                      )}
                      {canManage && !selected.grants_all && (
                        <button
                          className="ta-btn ta-btn-ghost"
                          disabled={saving}
                          onClick={() => void removeSelected()}
                        >
                          <Trash2 size={15} /> Xoá
                        </button>
                      )}
                    </div>
                  </div>

                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
                      gap: 12,
                      marginBottom: 18,
                    }}
                  >
                    <label style={{ display: "block" }}>
                      <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Tên chức vụ</span>
                      <input
                        className="ta-input"
                        value={draftName}
                        disabled={!canManage}
                        onChange={(event) => setDraftName(event.target.value)}
                      />
                    </label>
                    <label style={{ display: "block" }}>
                      <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Cấp trên</span>
                      <select
                        className="ta-input"
                        value={draftParent}
                        disabled={!canManage || selected.parent_id === null}
                        onChange={(event) => setDraftParent(event.target.value)}
                      >
                        <option value="">— Không có (chức vụ gốc) —</option>
                        {positions
                          .filter((item) => item.id !== selected.id)
                          .map((item) => (
                            <option key={item.id} value={item.id}>
                              {item.name}
                            </option>
                          ))}
                      </select>
                    </label>
                  </div>

                  <div
                    style={{
                      fontSize: 12,
                      fontWeight: 700,
                      color: "var(--text-muted)",
                      textTransform: "uppercase",
                      letterSpacing: "0.06em",
                      marginBottom: 10,
                    }}
                  >
                    Quyền hạn
                  </div>

                  {selected.grants_all ? (
                    <div
                      className="ta-card"
                      style={{ padding: 14, color: "var(--text-muted)", marginBottom: 18 }}
                    >
                      Đây là chức vụ gốc. Nó luôn có <strong>toàn bộ quyền</strong>, kể cả những
                      quyền được bổ sung ở các phiên bản sau, nên danh sách quyền không chỉnh sửa
                      được. Tạo một chức vụ khác nếu cần giới hạn.
                    </div>
                  ) : (
                    <div style={{ marginBottom: 18 }}>
                      {groupedCatalog.map(([group, items]) => (
                        <div key={group} style={{ marginBottom: 14 }}>
                          <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 6 }}>
                            {group}
                          </div>
                          <div
                            style={{
                              display: "grid",
                              gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
                              gap: 8,
                            }}
                          >
                            {items.map((permission) => {
                              const checked = draftPermissions.has(permission.code);
                              const grantable =
                                myPermissions.includes(permission.code) || !canManage;
                              return (
                                <label
                                  key={permission.code}
                                  title={
                                    grantable
                                      ? permission.description
                                      : "Bạn không có quyền này nên không thể cấp cho người khác."
                                  }
                                  style={{
                                    display: "flex",
                                    gap: 8,
                                    alignItems: "flex-start",
                                    padding: 10,
                                    borderRadius: 8,
                                    border: "1px solid var(--border, #E2E8F0)",
                                    opacity: grantable ? 1 : 0.5,
                                    cursor: canManage && grantable ? "pointer" : "not-allowed",
                                  }}
                                >
                                  <input
                                    type="checkbox"
                                    checked={checked}
                                    disabled={!canManage || !grantable}
                                    onChange={() => togglePermission(permission.code)}
                                    style={{ marginTop: 3 }}
                                  />
                                  <span style={{ minWidth: 0 }}>
                                    <strong style={{ fontSize: 13 }}>{permission.label}</strong>
                                    <span
                                      style={{
                                        display: "block",
                                        fontSize: 12,
                                        color: "var(--text-muted)",
                                      }}
                                    >
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
                  )}

                  <div
                    style={{
                      fontSize: 12,
                      fontWeight: 700,
                      color: "var(--text-muted)",
                      textTransform: "uppercase",
                      letterSpacing: "0.06em",
                      marginBottom: 8,
                    }}
                  >
                    <Users size={13} style={{ verticalAlign: "-2px" }} /> Người đang giữ chức vụ
                  </div>
                  {holders.length === 0 ? (
                    <div style={{ color: "var(--text-muted)", fontSize: 13 }}>
                      Chưa có ai giữ chức vụ này.
                    </div>
                  ) : (
                    <div style={{ overflowX: "auto" }}>
                      <table className="ta-table">
                        <thead>
                          <tr>
                            <th>Nhân viên</th>
                            <th>Phòng ban</th>
                            <th>Trạng thái</th>
                          </tr>
                        </thead>
                        <tbody>
                          {holders.map((holder) => (
                            <tr key={holder.id}>
                              <td>
                                <strong>{holder.full_name}</strong>
                                <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                                  {holder.email}
                                </div>
                              </td>
                              <td>{holder.department}</td>
                              <td>
                                <span
                                  className={`ta-badge ${
                                    holder.is_active ? "ta-badge-success" : "ta-badge-danger"
                                  }`}
                                >
                                  {holder.is_active ? "Hoạt động" : "Đã khóa"}
                                </span>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        </main>

        {showCreate && (
          <div
            role="dialog"
            aria-modal="true"
            style={{
              position: "fixed",
              inset: 0,
              background: "rgba(15,23,42,0.45)",
              display: "grid",
              placeItems: "center",
              zIndex: 50,
            }}
            onClick={() => setShowCreate(false)}
          >
            <div
              className="ta-card"
              style={{ padding: 20, width: "min(460px, 92vw)" }}
              onClick={(event) => event.stopPropagation()}
            >
              <h3 style={{ marginTop: 0, fontWeight: 800 }}>Thêm chức vụ</h3>
              <p style={{ color: "var(--text-muted)", fontSize: 13, marginTop: 0 }}>
                Chức vụ mới được tạo không có quyền nào. Tích quyền ở bảng chi tiết sau khi tạo.
              </p>
              <label style={{ display: "block", marginBottom: 10 }}>
                <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Tên chức vụ</span>
                <input
                  className="ta-input"
                  autoFocus
                  value={newPosition.name}
                  placeholder="Ví dụ: Trưởng phòng Kinh doanh"
                  onChange={(event) =>
                    setNewPosition((value) => ({ ...value, name: event.target.value }))
                  }
                />
              </label>
              <label style={{ display: "block", marginBottom: 16 }}>
                <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Cấp trên</span>
                <select
                  className="ta-input"
                  value={newPosition.parent_id}
                  onChange={(event) =>
                    setNewPosition((value) => ({ ...value, parent_id: event.target.value }))
                  }
                >
                  <option value="">— Không có —</option>
                  {positions.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
                <button className="ta-btn ta-btn-ghost" onClick={() => setShowCreate(false)}>
                  Huỷ
                </button>
                <button
                  className="ta-btn ta-btn-primary"
                  disabled={!newPosition.name.trim() || saving}
                  onClick={() => void createPosition()}
                >
                  Tạo
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
