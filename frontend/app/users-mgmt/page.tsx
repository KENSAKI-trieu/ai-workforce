"use client";

import axios from "axios";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Building2, Lock, Plus, Search, Unlock, UserPlus, Users } from "lucide-react";
import Sidebar from "@/components/Sidebar";
import api from "@/lib/api";
import { useAuthStore } from "@/store/useAuthStore";

interface UserItem {
  id: string;
  email: string;
  full_name: string;
  // Nhãn kỹ thuật suy ra từ chức vụ. Chức vụ mới là thứ được hiển thị và chỉnh sửa.
  role: string;
  position_id: string | null;
  position_name: string | null;
  department: string;
  is_active: boolean;
  created_at: string;
}

/** Chức vụ do chính công ty định nghĩa trong Sơ đồ tổ chức. */
interface Position {
  id: string;
  parent_id: string | null;
  name: string;
  slug: string;
  permissions: string[];
  grants_all: boolean;
  is_active: boolean;
  sort_order: number;
  holder_count: number;
}

interface PositionTreeResponse {
  positions: Position[];
  my_permissions: string[];
}

interface Department {
  id: string;
  code: string;
  name: string;
  member_count: number;
  is_active: boolean;
}

interface PaginatedUsersResponse {
  items: UserItem[];
  pagination: {
    page: number;
    page_size: number;
    total: number;
    total_pages: number;
  };
}

interface UpdatedUserResponse {
  message: string;
  user: UserItem;
}

interface AssignPositionResponse {
  user_id: string;
  position_id: string;
  position_name: string;
}

const PAGE_SIZE = 30;

function messageFrom(error: unknown) {
  return axios.isAxiosError(error)
    ? String(error.response?.data?.detail || error.message)
    : "Không thể xử lý yêu cầu.";
}

export default function UsersManagementPage() {
  const router = useRouter();
  const { isAuthenticated, hasHydrated, user: currentUser } = useAuthStore();
  const [users, setUsers] = useState<UserItem[]>([]);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [myPermissions, setMyPermissions] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [departmentFilter, setDepartmentFilter] = useState("");
  const [positionFilter, setPositionFilter] = useState("");
  const [page, setPage] = useState(1);
  const [totalUsers, setTotalUsers] = useState(0);
  const [updatingUserIds, setUpdatingUserIds] = useState<Set<string>>(new Set());
  const [showAdd, setShowAdd] = useState(false);
  const [showDepartment, setShowDepartment] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [employee, setEmployee] = useState({
    full_name: "", email: "", password: "Password123!", position_id: "", department: "SALES",
  });
  const [newDepartment, setNewDepartment] = useState({ code: "", name: "" });

  const fetchUsers = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const { data } = await api.get<PaginatedUsersResponse>("/api/v1/users-mgmt", {
        params: {
          page,
          page_size: PAGE_SIZE,
          q: debouncedQuery || undefined,
          department: departmentFilter || undefined,
          position_id: positionFilter || undefined,
        },
      });
      setUsers(data.items);
      setTotalUsers(data.pagination.total);
    } catch (reason) {
      setError(messageFrom(reason));
    } finally {
      setLoading(false);
    }
  }, [debouncedQuery, departmentFilter, page, positionFilter]);

  /** Vai trò lấy từ cây chức vụ của công ty, không phải danh sách cứng trong code. */
  const fetchPositions = useCallback(async () => {
    try {
      const { data } = await api.get<PositionTreeResponse>("/api/v1/positions");
      setPositions(data.positions);
      setMyPermissions(data.my_permissions);
      setEmployee((value) => {
        if (data.positions.some((item) => item.id === value.position_id)) return value;
        const fallback = data.positions.find((item) => item.slug === "employee" && item.is_active)
          ?? data.positions.find((item) => item.is_active && !item.grants_all);
        return { ...value, position_id: fallback?.id ?? "" };
      });
    } catch (reason) {
      setError(messageFrom(reason));
    }
  }, []);

  const fetchDepartments = useCallback(async () => {
    try {
      const { data } = await api.get<Department[]>("/api/v1/workspace/departments");
      setDepartments(data);
      setEmployee((value) =>
        data.some((item) => item.code === value.department)
          ? value
          : { ...value, department: data[0]?.code || "ALL" },
      );
    } catch (reason) {
      setError(messageFrom(reason));
    }
  }, []);

  useEffect(() => {
    if (!hasHydrated) return;
    if (!isAuthenticated) {
      router.replace("/login");
      return;
    }
    const timer = window.setTimeout(() => {
      void fetchDepartments();
      void fetchPositions();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [fetchDepartments, fetchPositions, hasHydrated, isAuthenticated, router]);

  useEffect(() => {
    if (!hasHydrated || !isAuthenticated) return;
    const timer = window.setTimeout(() => void fetchUsers(), 0);
    return () => window.clearTimeout(timer);
  }, [fetchUsers, hasHydrated, isAuthenticated]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setPage(1);
      setDebouncedQuery(query.trim());
    }, 300);
    return () => window.clearTimeout(timer);
  }, [query]);

  const createEmployee = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    try {
      await api.post("/api/v1/users-mgmt", {
        ...employee,
        position_id: employee.position_id || undefined,
      });
      setEmployee((value) => ({ ...value, full_name: "", email: "", password: "Password123!" }));
      setShowAdd(false);
      setPage(1);
      await Promise.all([fetchUsers(), fetchDepartments(), fetchPositions()]);
    } catch (reason) {
      setError(messageFrom(reason));
    }
  };

  const updateUser = async (userId: string, payload: Partial<Pick<UserItem, "is_active" | "department">>) => {
    const previousIndex = users.findIndex((item) => item.id === userId);
    const previousUser = users[previousIndex];
    if (!previousUser || updatingUserIds.has(userId)) return;

    const optimisticUser = { ...previousUser, ...payload };
    const leavesCurrentFilter = Boolean(
      departmentFilter && optimisticUser.department !== departmentFilter
    );
    const departmentChanged = payload.department !== undefined && payload.department !== previousUser.department;

    setError(null);
    setUpdatingUserIds((current) => new Set(current).add(userId));
    setUsers((current) => leavesCurrentFilter
      ? current.filter((item) => item.id !== userId)
      : current.map((item) => item.id === userId ? optimisticUser : item));
    if (leavesCurrentFilter) setTotalUsers((current) => Math.max(0, current - 1));
    if (departmentChanged) {
      setDepartments((current) => current.map((department) => {
        if (department.code === previousUser.department) {
          return { ...department, member_count: Math.max(0, department.member_count - 1) };
        }
        if (department.code === payload.department) {
          return { ...department, member_count: department.member_count + 1 };
        }
        return department;
      }));
    }

    try {
      const { data } = await api.patch<UpdatedUserResponse>(
        `/api/v1/users-mgmt/${userId}/status`,
        payload,
      );
      if (!leavesCurrentFilter) {
        setUsers((current) => current.map((item) => item.id === userId ? data.user : item));
      }
    } catch (reason) {
      setUsers((current) => {
        if (current.some((item) => item.id === userId)) {
          return current.map((item) => item.id === userId ? previousUser : item);
        }
        const restored = [...current];
        restored.splice(Math.min(previousIndex, restored.length), 0, previousUser);
        return restored;
      });
      if (leavesCurrentFilter) setTotalUsers((current) => current + 1);
      if (departmentChanged) {
        setDepartments((current) => current.map((department) => {
          if (department.code === previousUser.department) {
            return { ...department, member_count: department.member_count + 1 };
          }
          if (department.code === payload.department) {
            return { ...department, member_count: Math.max(0, department.member_count - 1) };
          }
          return department;
        }));
      }
      setError(messageFrom(reason));
    } finally {
      setUpdatingUserIds((current) => {
        const next = new Set(current);
        next.delete(userId);
        return next;
      });
    }
  };

  /** Đổi chức vụ đi qua cây tổ chức, nên quyền thật đổi theo chứ không chỉ đổi nhãn. */
  const assignPosition = async (userId: string, positionId: string) => {
    const previousIndex = users.findIndex((item) => item.id === userId);
    const previousUser = users[previousIndex];
    const position = positions.find((item) => item.id === positionId);
    if (!previousUser || !position || updatingUserIds.has(userId)) return;
    if (previousUser.position_id === positionId) return;

    const leavesCurrentFilter = Boolean(positionFilter && positionFilter !== positionId);

    setError(null);
    setUpdatingUserIds((current) => new Set(current).add(userId));
    setUsers((current) => leavesCurrentFilter
      ? current.filter((item) => item.id !== userId)
      : current.map((item) => item.id === userId
        ? { ...item, position_id: position.id, position_name: position.name }
        : item));
    if (leavesCurrentFilter) setTotalUsers((current) => Math.max(0, current - 1));

    try {
      const { data } = await api.put<AssignPositionResponse>(
        `/api/v1/positions/assign/${userId}`,
        { position_id: positionId },
      );
      if (!leavesCurrentFilter) {
        setUsers((current) => current.map((item) => item.id === userId
          ? { ...item, position_id: data.position_id, position_name: data.position_name }
          : item));
      }
      // Số người giữ mỗi chức vụ vừa đổi ở cả hai đầu.
      await fetchPositions();
    } catch (reason) {
      setUsers((current) => {
        if (current.some((item) => item.id === userId)) {
          return current.map((item) => item.id === userId ? previousUser : item);
        }
        const restored = [...current];
        restored.splice(Math.min(previousIndex, restored.length), 0, previousUser);
        return restored;
      });
      if (leavesCurrentFilter) setTotalUsers((current) => current + 1);
      setError(messageFrom(reason));
    } finally {
      setUpdatingUserIds((current) => {
        const next = new Set(current);
        next.delete(userId);
        return next;
      });
    }
  };

  const createDepartment = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    try {
      await api.post("/api/v1/workspace/departments", {
        code: newDepartment.code.trim().toUpperCase().replace(/\s+/g, "_"),
        name: newDepartment.name.trim(),
      });
      setNewDepartment({ code: "", name: "" });
      setShowDepartment(false);
      await fetchDepartments();
    } catch (reason) {
      setError(messageFrom(reason));
    }
  };

  if (!hasHydrated || !isAuthenticated) return null;
  // Hỏi người dùng được làm gì, thay vì đoán qua tên vai trò — công ty có thể đặt tên
  // chức vụ bất kỳ, nên so khớp chuỗi role sẽ sai ngay khi họ đổi tên.
  const canManageUsers = myPermissions.includes("users.manage");
  const canAssignPosition = myPermissions.includes("users.position.assign");
  const assignablePositions = positions.filter((item) => item.is_active);
  const totalPages = Math.max(1, Math.ceil(totalUsers / PAGE_SIZE));

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "var(--body-bg)" }}>
      <Sidebar />
      <div style={{ flex: 1, minWidth: 0 }}>
        <header className="ta-topbar">
          <div className="breadcrumb"><span>Home</span><span className="breadcrumb-sep">›</span><span className="breadcrumb-current">Công ty & nhân viên</span></div>
          {canManageUsers && <div style={{ display: "flex", gap: 8 }}>
            <button className="ta-btn ta-btn-ghost" onClick={() => setShowDepartment(true)}><Building2 size={15} /> Thêm phòng ban</button>
            <button className="ta-btn ta-btn-primary" onClick={() => setShowAdd(true)}><UserPlus size={15} /> Thêm nhân viên</button>
          </div>}
        </header>
        <main style={{ padding: "24px 32px" }}>
          <h1 style={{ display: "flex", alignItems: "center", gap: 9, fontSize: "1.5rem", fontWeight: 800 }}><Users size={24} color="var(--primary)" /> Quản lý công ty & phân quyền</h1>
          <p style={{ color: "var(--text-muted)", margin: "6px 0 18px" }}>Vai trò lấy từ cây chức vụ của công ty. Tạo hoặc đổi quyền của chức vụ trong Sơ đồ tổ chức.</p>
          {error && <div className="ta-card" style={{ color: "#B91C1C", padding: 13, marginBottom: 14 }}>{error}</div>}

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 10, marginBottom: 18 }}>
            {departments.map((department) => (
              <div className="ta-card" key={department.id} style={{ padding: 14 }}>
                <strong>{department.name}</strong><div style={{ fontSize: 12, color: "var(--text-muted)" }}>{department.code} · {department.member_count} thành viên</div>
              </div>
            ))}
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "minmax(260px, 1fr) 220px 190px", gap: 10, marginBottom: 14 }}>
            <div style={{ position: "relative" }}>
              <Search size={15} style={{ position: "absolute", top: 12, left: 11 }} />
              <input className="ta-input" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Tìm theo tên hoặc email..." style={{ paddingLeft: 34 }} />
            </div>
            <select
              className="ta-input"
              value={departmentFilter}
              onChange={(event) => { setDepartmentFilter(event.target.value); setPage(1); }}
            >
              <option value="">Tất cả phòng ban</option>
              {departments.map((department) => (
                <option key={department.id} value={department.code}>{department.name} ({department.code})</option>
              ))}
            </select>
            <select
              className="ta-input"
              value={positionFilter}
              onChange={(event) => { setPositionFilter(event.target.value); setPage(1); }}
            >
              <option value="">Tất cả vai trò</option>
              {positions.map((position) => (
                <option key={position.id} value={position.id}>{position.name}</option>
              ))}
            </select>
          </div>
          <div className="ta-card" style={{ overflowX: "auto" }}>
            <table className="ta-table">
              <thead><tr><th>Nhân viên</th><th>Phòng ban</th><th>Vai trò</th><th>Trạng thái</th><th>Thao tác</th></tr></thead>
              <tbody>
                {loading ? <tr><td colSpan={5}>Đang tải...</td></tr> : users.length === 0 ? (
                  <tr><td colSpan={5} style={{ textAlign: "center", color: "var(--text-muted)", padding: 24 }}>Không tìm thấy nhân viên phù hợp.</td></tr>
                ) : users.map((item) => (
                  <tr key={item.id}>
                    <td><strong>{item.full_name}</strong><div style={{ fontSize: 12, color: "var(--text-muted)" }}>{item.email}</div></td>
                    <td>
                      <select className="ta-input" value={item.department} disabled={!canManageUsers || updatingUserIds.has(item.id)} onChange={(event) => void updateUser(item.id, { department: event.target.value })}>
                        <option value="ALL">ALL</option>
                        {departments.filter((department) => department.is_active).map((department) => <option key={department.id} value={department.code}>{department.code}</option>)}
                      </select>
                    </td>
                    <td>
                      <select className="ta-input" value={item.position_id ?? ""} disabled={!canAssignPosition || updatingUserIds.has(item.id)} onChange={(event) => void assignPosition(item.id, event.target.value)}>
                        {/* Người chưa có chức vụ, hoặc đang giữ chức vụ đã vô hiệu hoá, vẫn phải đọc được dòng của mình. */}
                        {!item.position_id && <option value="">— Chưa có chức vụ —</option>}
                        {item.position_id && !assignablePositions.some((position) => position.id === item.position_id) && (
                          <option value={item.position_id}>{item.position_name ?? item.role}</option>
                        )}
                        {assignablePositions.map((position) => (
                          <option key={position.id} value={position.id}>{position.name}</option>
                        ))}
                      </select>
                    </td>
                    <td><span className={`ta-badge ${item.is_active ? "ta-badge-success" : "ta-badge-danger"}`}>{item.is_active ? "Hoạt động" : "Đã khóa"}</span></td>
                    <td>
                      {canManageUsers && item.id !== currentUser?.id && <button className="ta-btn ta-btn-ghost" disabled={updatingUserIds.has(item.id)} onClick={() => void updateUser(item.id, { is_active: !item.is_active })}>
                        {item.is_active ? <Lock size={14} /> : <Unlock size={14} />} {item.is_active ? "Khóa" : "Mở"}
                      </button>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 14, color: "var(--text-muted)", fontSize: 13 }}>
            <span>{totalUsers.toLocaleString("vi-VN")} nhân viên · 30 người/trang</span>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <button className="ta-btn ta-btn-ghost" disabled={page <= 1 || loading} onClick={() => setPage((current) => Math.max(1, current - 1))}>Trước</button>
              <strong style={{ color: "var(--text-dark)" }}>Trang {page} / {totalPages}</strong>
              <button className="ta-btn ta-btn-ghost" disabled={page >= totalPages || loading} onClick={() => setPage((current) => Math.min(totalPages, current + 1))}>Sau</button>
            </div>
          </div>
        </main>
      </div>

      {showAdd && <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.45)", display: "grid", placeItems: "center", zIndex: 100 }}>
        <form className="ta-card" onSubmit={createEmployee} style={{ width: 430, padding: 22, display: "grid", gap: 12 }}>
          <h2 style={{ fontWeight: 750 }}>Thêm nhân viên</h2>
          <input className="ta-input" placeholder="Họ và tên" value={employee.full_name} onChange={(event) => setEmployee({ ...employee, full_name: event.target.value })} required />
          <input className="ta-input" type="email" placeholder="Email công ty" value={employee.email} onChange={(event) => setEmployee({ ...employee, email: event.target.value })} required />
          <input className="ta-input" type="password" minLength={8} value={employee.password} onChange={(event) => setEmployee({ ...employee, password: event.target.value })} required />
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
            <select className="ta-input" value={employee.position_id} onChange={(event) => setEmployee({ ...employee, position_id: event.target.value })} required>
              {assignablePositions.map((position) => (
                <option key={position.id} value={position.id}>{position.name}</option>
              ))}
            </select>
            <select className="ta-input" value={employee.department} onChange={(event) => setEmployee({ ...employee, department: event.target.value })}>{departments.filter((item) => item.is_active).map((item) => <option key={item.id} value={item.code}>{item.code}</option>)}</select>
          </div>
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}><button type="button" className="ta-btn ta-btn-ghost" onClick={() => setShowAdd(false)}>Hủy</button><button className="ta-btn ta-btn-primary"><Plus size={14} /> Tạo tài khoản</button></div>
        </form>
      </div>}

      {showDepartment && <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.45)", display: "grid", placeItems: "center", zIndex: 100 }}>
        <form className="ta-card" onSubmit={createDepartment} style={{ width: 400, padding: 22, display: "grid", gap: 12 }}>
          <h2 style={{ fontWeight: 750 }}>Tạo phòng ban</h2>
          <input className="ta-input" placeholder="Mã, ví dụ MARKETING" value={newDepartment.code} onChange={(event) => setNewDepartment({ ...newDepartment, code: event.target.value })} required />
          <input className="ta-input" placeholder="Tên phòng ban" value={newDepartment.name} onChange={(event) => setNewDepartment({ ...newDepartment, name: event.target.value })} required />
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}><button type="button" className="ta-btn ta-btn-ghost" onClick={() => setShowDepartment(false)}>Hủy</button><button className="ta-btn ta-btn-primary"><Plus size={14} /> Tạo</button></div>
        </form>
      </div>}
    </div>
  );
}
