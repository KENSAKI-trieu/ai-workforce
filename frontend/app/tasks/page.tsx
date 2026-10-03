"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/store/useAuthStore";
import Sidebar from "@/components/Sidebar";
import api from "@/lib/api";
import {
  Ticket,
  Plus,
  Search,
  Kanban,
  Table as TableIcon,
  List as ListIcon,
  User as UserIcon,
  Bot,
  MoreHorizontal,
  MessageSquare,
  X,
  Edit2,
  Trash2,
  Calendar as CalendarIcon,
  Clock,
  Send,
  AlertTriangle,
  CheckCircle2,
  ArrowRight,
} from "lucide-react";

interface TaskItem {
  id: string;
  title: string;
  description: string | null;
  creator: { id: string; name: string } | null;
  assignee: { id: string; name: string } | null;
  ai_agent: { id: string; name: string; emoji: string } | null;
  priority: string;
  due_date: string | null;
  status: string;
  allowed_transitions?: string[];
  comments_count: number;
  created_at: string;
}

interface AgentItem {
  id: string;
  name: string;
  role_code: string;
  avatar_emoji: string;
}

interface UserMember {
  id: string;
  full_name: string;
  email: string;
  role: string;
}

interface PaginatedUsersResponse {
  items: UserMember[];
}

interface TaskCommentItem {
  id: string;
  user_name: string;
  content: string;
  created_at: string;
}

interface TaskDetails {
  comments: TaskCommentItem[];
}

const KANBAN_COLUMNS = [
  { key: "DRAFT", title: "Nháp (Draft)", color: "#64748B", bg: "#F8FAFC" },
  { key: "PENDING", title: "Chờ Xử Lý", color: "#3B82F6", bg: "#EFF6FF" },
  { key: "RUNNING", title: "Đang Chạy", color: "#8B5CF6", bg: "#F5F3FF" },
  { key: "WAITING_APPROVAL", title: "Chờ Phê Duyệt", color: "#F59E0B", bg: "#FEF3C7" },
  { key: "COMPLETED", title: "Hoàn Thành", color: "#10B981", bg: "#ECFDF5" },
];

const TASK_TRANSITIONS: Record<string, string[]> = {
  DRAFT: ["PENDING", "CANCELLED"],
  PENDING: ["RUNNING", "CANCELLED", "OVERDUE"],
  RUNNING: ["WAITING_APPROVAL", "COMPLETED", "FAILED", "CANCELLED", "OVERDUE"],
  WAITING_APPROVAL: ["RUNNING", "COMPLETED", "FAILED", "CANCELLED", "OVERDUE"],
  FAILED: ["PENDING", "CANCELLED"],
  OVERDUE: ["RUNNING", "COMPLETED", "CANCELLED"],
  COMPLETED: [],
  CANCELLED: [],
};

const TASK_STATUS_LABELS: Record<string, string> = {
  DRAFT: "Draft",
  PENDING: "Pending",
  RUNNING: "Running",
  WAITING_APPROVAL: "Waiting Approval",
  COMPLETED: "Completed",
  FAILED: "Failed",
  CANCELLED: "Cancelled",
  OVERDUE: "Overdue",
};

function getAllowedTransitions(task: TaskItem): string[] {
  return task.allowed_transitions ?? TASK_TRANSITIONS[task.status] ?? [];
}

function getStatusOptions(task: TaskItem): string[] {
  return [task.status, ...getAllowedTransitions(task).filter((status) => status !== task.status)];
}

function canTransition(task: TaskItem, nextStatus: string): boolean {
  return nextStatus === task.status || getAllowedTransitions(task).includes(nextStatus);
}

export default function TaskManagementPage() {
  const router = useRouter();
  const { isAuthenticated, hasHydrated, user } = useAuthStore();

  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [agents, setAgents] = useState<AgentItem[]>([]);
  const [usersList, setUsersList] = useState<UserMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [viewMode, setViewMode] = useState<"kanban" | "table" | "list">("kanban");
  const [filterPriority, setFilterPriority] = useState<string>("ALL");
  const [searchQuery, setSearchQuery] = useState("");

  // Drag and Drop State
  const [draggingTaskId, setDraggingTaskId] = useState<string | null>(null);
  const [dragOverColumn, setDragOverColumn] = useState<string | null>(null);
  const [dragOverTaskId, setDragOverTaskId] = useState<string | null>(null);

  // Modals state
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showEditModal, setShowEditModal] = useState(false);
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [showDetailModal, setShowDetailModal] = useState(false);

  // Active Task for Edit / Delete / Detail
  const [activeTask, setActiveTask] = useState<TaskItem | null>(null);
  const [taskDetails, setTaskDetails] = useState<TaskDetails | null>(null);
  const [commentInput, setCommentInput] = useState("");
  const [submittingComment, setSubmittingComment] = useState(false);

  // Form State (used for Create & Edit)
  const [formTitle, setFormTitle] = useState("");
  const [formDesc, setFormDesc] = useState("");
  const [formPriority, setFormPriority] = useState("MEDIUM");
  const [formStatus, setFormStatus] = useState("PENDING");
  const [formAssigneeType, setFormAssigneeType] = useState<"NONE" | "USER" | "AGENT">("NONE");
  const [formAssigneeId, setFormAssigneeId] = useState("");
  const [formDueDate, setFormDueDate] = useState("");

  useEffect(() => {
    const hasToken = typeof window !== "undefined" && Boolean(localStorage.getItem("access_token"));
    if (!isAuthenticated && !hasToken) {
      router.push("/login");
      return;
    }
    const timer = window.setTimeout(() => void fetchData(), 0);
    return () => window.clearTimeout(timer);
  }, [isAuthenticated]);

  async function fetchData() {
    try {
      setLoading(true);
      const [tasksRes, agentsRes, usersRes] = await Promise.allSettled([
        api.get("/api/v1/tasks"),
        api.get("/api/v1/agents"),
        api.get<PaginatedUsersResponse>("/api/v1/users-mgmt", { params: { page: 1, page_size: 100 } }),
      ]);

      if (tasksRes.status === "fulfilled") setTasks(tasksRes.value.data);
      if (agentsRes.status === "fulfilled") setAgents(agentsRes.value.data);
      if (usersRes.status === "fulfilled") setUsersList(usersRes.value.data.items);
    } catch (err) {
      console.error("Failed to fetch task management data:", err);
    } finally {
      setLoading(false);
    }
  }

  // ── Open Create Modal ──
  const handleOpenCreate = () => {
    setFormTitle("");
    setFormDesc("");
    setFormPriority("MEDIUM");
    setFormStatus("PENDING");
    setFormAssigneeType("NONE");
    setFormAssigneeId("");
    setFormDueDate("");
    setShowCreateModal(true);
  };

  // ── Create Task Submit ──
  const handleCreateTask = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formTitle.trim()) return;

    try {
      const payload: Record<string, string | null> = {
        title: formTitle,
        description: formDesc || null,
        priority: formPriority,
        status: formStatus,
        due_date: formDueDate ? new Date(formDueDate).toISOString() : null,
      };

      if (formAssigneeType === "USER" && formAssigneeId) {
        payload.assignee_id = formAssigneeId;
      } else if (formAssigneeType === "AGENT" && formAssigneeId) {
        payload.ai_agent_id = formAssigneeId;
      }

      await api.post("/api/v1/tasks", payload);
      setShowCreateModal(false);
      fetchData();
    } catch (err) {
      console.error("Failed to create task:", err);
      alert("Không thể tạo task. Vui lòng kiểm tra lại thông tin!");
    }
  };

  // ── Open Edit Modal ──
  const handleOpenEdit = (task: TaskItem, e?: React.MouseEvent) => {
    if (e) e.stopPropagation();
    setActiveTask(task);
    setFormTitle(task.title);
    setFormDesc(task.description || "");
    setFormPriority(task.priority || "MEDIUM");
    setFormStatus(task.status || "PENDING");
    setFormDueDate(task.due_date ? task.due_date.substring(0, 10) : "");

    if (task.assignee) {
      setFormAssigneeType("USER");
      setFormAssigneeId(task.assignee.id);
    } else if (task.ai_agent) {
      setFormAssigneeType("AGENT");
      setFormAssigneeId(task.ai_agent.id);
    } else {
      setFormAssigneeType("NONE");
      setFormAssigneeId("");
    }

    setShowEditModal(true);
  };

  // ── Edit Task Submit ──
  const handleEditTask = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!activeTask || !formTitle.trim()) return;

    try {
      const payload: Record<string, string | null> = {
        title: formTitle,
        description: formDesc || null,
        priority: formPriority,
        status: formStatus,
        due_date: formDueDate ? new Date(formDueDate).toISOString() : null,
        assignee_id: formAssigneeType === "USER" ? formAssigneeId : null,
        ai_agent_id: formAssigneeType === "AGENT" ? formAssigneeId : null,
      };

      await api.patch(`/api/v1/tasks/${activeTask.id}`, payload);
      setShowEditModal(false);
      setActiveTask(null);
      fetchData();
    } catch (err) {
      console.error("Failed to update task:", err);
      alert("Không thể cập nhật task!");
    }
  };

  // ── Open Delete Confirmation ──
  const handleOpenDelete = (task: TaskItem, e?: React.MouseEvent) => {
    if (e) e.stopPropagation();
    if (!["DRAFT", "COMPLETED"].includes(task.status)) {
      alert("Chỉ task DRAFT hoặc COMPLETED mới được xóa. Với task đang xử lý, hãy chuyển sang CANCELLED.");
      return;
    }
    setActiveTask(task);
    setShowDeleteModal(true);
  };

  // ── Confirm Delete Task ──
  const handleDeleteTask = async () => {
    if (!activeTask) return;
    try {
      await api.delete(`/api/v1/tasks/${activeTask.id}`);
      setTasks((prev) => prev.filter((t) => t.id !== activeTask.id));
      setShowDeleteModal(false);
      setActiveTask(null);
    } catch (err) {
      console.error("Failed to delete task:", err);
      alert("Không thể xóa task!");
    }
  };

  // ── Open Detail Modal ──
  const handleOpenDetail = async (task: TaskItem) => {
    setActiveTask(task);
    setShowDetailModal(true);
    try {
      const { data } = await api.get(`/api/v1/tasks/${task.id}`);
      setTaskDetails(data);
    } catch (err) {
      console.error("Failed to fetch task detail:", err);
    }
  };

  // ── Submit Comment ──
  const handleAddComment = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!activeTask || !commentInput.trim()) return;

    try {
      setSubmittingComment(true);
      await api.post(`/api/v1/tasks/${activeTask.id}/comments`, { content: commentInput });
      setCommentInput("");
      const { data } = await api.get(`/api/v1/tasks/${activeTask.id}`);
      setTaskDetails(data);
    } catch (err) {
      console.error("Failed to add comment:", err);
    } finally {
      setSubmittingComment(false);
    }
  };

  // ── Status Select Update (Inline) ──
  const handleUpdateStatus = async (taskId: string, nextStatus: string) => {
    const task = tasks.find((item) => item.id === taskId);
    if (!task || !canTransition(task, nextStatus)) {
      alert(`Không thể chuyển task từ ${task?.status ?? "trạng thái hiện tại"} sang ${nextStatus}.`);
      return;
    }
    try {
      setTasks((prev) =>
        prev.map((t) => (t.id === taskId ? { ...t, status: nextStatus } : t))
      );
      await api.patch(`/api/v1/tasks/${taskId}`, { status: nextStatus });
    } catch (err) {
      console.error("Failed to update status:", err);
      fetchData(); // Rollback on error
    }
  };

  // ── HTML5 Drag & Drop Handlers ──
  const handleDragStart = (e: React.DragEvent, taskId: string) => {
    e.dataTransfer.setData("text/plain", taskId);
    e.dataTransfer.effectAllowed = "move";
    setDraggingTaskId(taskId);
  };

  const handleDragOverColumn = (e: React.DragEvent, columnKey: string) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    if (dragOverColumn !== columnKey) {
      setDragOverColumn(columnKey);
    }
  };

  const handleDragOverCard = (e: React.DragEvent, targetTaskId: string) => {
    e.preventDefault();
    e.stopPropagation();
    e.dataTransfer.dropEffect = "move";
    if (dragOverTaskId !== targetTaskId) {
      setDragOverTaskId(targetTaskId);
    }
  };

  const handleDragLeaveColumn = (e: React.DragEvent, columnKey: string) => {
    e.preventDefault();
    if (dragOverColumn === columnKey) {
      setDragOverColumn(null);
    }
  };

  const handleDragLeaveCard = (e: React.DragEvent, targetTaskId: string) => {
    e.preventDefault();
    e.stopPropagation();
    if (dragOverTaskId === targetTaskId) {
      setDragOverTaskId(null);
    }
  };

  const handleDrop = async (e: React.DragEvent, targetColumnKey: string, targetTaskId?: string) => {
    e.preventDefault();
    e.stopPropagation();

    setDragOverColumn(null);
    setDragOverTaskId(null);

    const taskId = e.dataTransfer.getData("text/plain") || draggingTaskId;
    setDraggingTaskId(null);

    if (!taskId) return;

    const draggedTaskIndex = tasks.findIndex((t) => t.id === taskId);
    if (draggedTaskIndex === -1) return;

    const draggedTask = tasks[draggedTaskIndex];
    const statusChanged = draggedTask.status !== targetColumnKey;

    if (statusChanged && !canTransition(draggedTask, targetColumnKey)) {
      alert(`Không thể chuyển task từ ${draggedTask.status} sang ${targetColumnKey}.`);
      return;
    }

    const updatedTask = { ...draggedTask, status: targetColumnKey };
    const remainingTasks = tasks.filter((t) => t.id !== taskId);

    let newTasks: TaskItem[] = [];

    if (targetTaskId && targetTaskId !== taskId) {
      const targetIndex = remainingTasks.findIndex((t) => t.id === targetTaskId);
      if (targetIndex !== -1) {
        remainingTasks.splice(targetIndex, 0, updatedTask);
        newTasks = remainingTasks;
      } else {
        newTasks = [...remainingTasks, updatedTask];
      }
    } else {
      newTasks = [...remainingTasks, updatedTask];
    }

    setTasks(newTasks);

    if (statusChanged) {
      try {
        await api.patch(`/api/v1/tasks/${taskId}`, { status: targetColumnKey });
      } catch (err) {
        console.error("Drag & drop update failed:", err);
        fetchData();
      }
    }
  };

  const filteredTasks = tasks.filter((t) => {
    const matchesSearch =
      t.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
      (t.description && t.description.toLowerCase().includes(searchQuery.toLowerCase()));
    const matchesPriority = filterPriority === "ALL" || t.priority === filterPriority;
    return matchesSearch && matchesPriority;
  });

  if (!hasHydrated || !isAuthenticated) return null;

  return (
    <div className="app-fullheight" style={{ display: "flex", height: "100vh", overflow: "hidden", background: "var(--body-bg)" }}>
      <Sidebar />

      <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", minWidth: 0 }}>
        {/* Topbar */}
        <header className="ta-topbar">
          <div className="breadcrumb">
            <span>Home</span>
            <span className="breadcrumb-sep">›</span>
            <span className="breadcrumb-current">Quản Lý Task & Kanban Workflows</span>
          </div>

          <button className="ta-btn ta-btn-primary" onClick={handleOpenCreate}>
            <Plus size={16} /> Tạo Task Mới
          </button>
        </header>

        {/* Main Workspace */}
        <main className="page-main" style={{ flex: 1, overflowY: "auto", padding: "24px 32px" }}>
          {/* Header & Controls */}
          <div className="wrap-mobile" style={{ marginBottom: "20px", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <div>
              <h1 style={{ fontSize: "1.5rem", fontWeight: 800, color: "var(--text-dark)", display: "flex", alignItems: "center", gap: "10px" }}>
                <Ticket size={24} color="var(--primary)" /> Quản Lý Công Việc (Kanban & Task CRUD)
              </h1>
              <p style={{ fontSize: "0.85rem", color: "var(--text-muted)", marginTop: "4px" }}>
                Kéo thả các công việc để thay đổi tiến độ xử lý thực tế từ CSDL PostgreSQL.
              </p>
            </div>

            {/* View Switcher */}
            <div className="scroll-x-mobile" style={{ display: "flex", background: "#FFFFFF", padding: "4px", borderRadius: "10px", border: "1px solid var(--border)" }}>
              <button
                onClick={() => setViewMode("kanban")}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "6px",
                  padding: "6px 14px",
                  borderRadius: "6px",
                  border: "none",
                  background: viewMode === "kanban" ? "var(--primary)" : "transparent",
                  color: viewMode === "kanban" ? "#FFF" : "var(--text-muted)",
                  fontWeight: 600,
                  fontSize: "0.82rem",
                  cursor: "pointer",
                  transition: "all 0.2s ease",
                }}
              >
                <Kanban size={15} /> Bảng Kanban (Kéo thả)
              </button>
              <button
                onClick={() => setViewMode("table")}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "6px",
                  padding: "6px 14px",
                  borderRadius: "6px",
                  border: "none",
                  background: viewMode === "table" ? "var(--primary)" : "transparent",
                  color: viewMode === "table" ? "#FFF" : "var(--text-muted)",
                  fontWeight: 600,
                  fontSize: "0.82rem",
                  cursor: "pointer",
                  transition: "all 0.2s ease",
                }}
              >
                <TableIcon size={15} /> Bảng Chi Tiết
              </button>
              <button
                onClick={() => setViewMode("list")}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "6px",
                  padding: "6px 14px",
                  borderRadius: "6px",
                  border: "none",
                  background: viewMode === "list" ? "var(--primary)" : "transparent",
                  color: viewMode === "list" ? "#FFF" : "var(--text-muted)",
                  fontWeight: 600,
                  fontSize: "0.82rem",
                  cursor: "pointer",
                  transition: "all 0.2s ease",
                }}
              >
                <ListIcon size={15} /> Danh Sách
              </button>
            </div>
          </div>

          {/* Search and Filters */}
          <div className="wrap-mobile" style={{ display: "flex", alignItems: "center", gap: "14px", marginBottom: "20px" }}>
            <div style={{ position: "relative", flex: 1, maxWidth: "380px" }}>
              <Search size={16} style={{ position: "absolute", left: "12px", top: "50%", transform: "translateY(-50%)", color: "var(--text-light)" }} />
              <input
                type="text"
                placeholder="Tìm tên task, nội dung công việc..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="ta-input"
                style={{ paddingLeft: "36px", height: "38px", fontSize: "0.85rem" }}
              />
            </div>

            <select
              value={filterPriority}
              onChange={(e) => setFilterPriority(e.target.value)}
              className="ta-input"
              style={{ width: "170px", height: "38px", fontSize: "0.85rem" }}
            >
              <option value="ALL">Mọi độ ưu tiên</option>
              <option value="LOW">Độ ưu tiên: Thấp</option>
              <option value="MEDIUM">Độ ưu tiên: Vừa</option>
              <option value="HIGH">Độ ưu tiên: Cao</option>
              <option value="URGENT">Khẩn cấp (Urgent)</option>
            </select>
          </div>

          {/* ── View 1: KANBAN BOARD WITH DRAG & DROP ── */}
          {viewMode === "kanban" && (
            <div className="kanban-board"
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(5, 1fr)",
                gap: "16px",
                minWidth: "1100px",
                alignItems: "start",
              }}
            >
              {KANBAN_COLUMNS.map((col) => {
                const colTasks = filteredTasks.filter((t) => t.status === col.key);
                const isOver = dragOverColumn === col.key;

                return (
                  <div
                    key={col.key}
                    onDragOver={(e) => handleDragOverColumn(e, col.key)}
                    onDragLeave={(e) => handleDragLeaveColumn(e, col.key)}
                    onDrop={(e) => handleDrop(e, col.key)}
                    style={{
                      background: isOver ? "#EEF2FF" : "#FAFBFC",
                      borderRadius: "14px",
                      border: isOver ? "2px dashed var(--primary)" : "1px solid var(--border)",
                      padding: "16px",
                      minHeight: "580px",
                      transition: "all 0.15s ease",
                    }}
                  >
                    {/* Column Header */}
                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        marginBottom: "14px",
                        paddingBottom: "10px",
                        borderBottom: `3px solid ${col.color}`,
                      }}
                    >
                      <span style={{ fontWeight: 700, fontSize: "0.88rem", color: "var(--text-dark)" }}>
                        {col.title}
                      </span>
                      <span
                        style={{
                          fontSize: "0.75rem",
                          padding: "2px 8px",
                          borderRadius: "99px",
                          background: "#EEF2FF",
                          color: "var(--primary)",
                          fontWeight: 700,
                        }}
                      >
                        {colTasks.length}
                      </span>
                    </div>

                    {/* Column Tasks */}
                    <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                      {colTasks.map((task) => {
                        const isDraggingThis = draggingTaskId === task.id;
                        const isDragOverThis = dragOverTaskId === task.id;

                        return (
                          <div
                            key={task.id}
                            draggable={getAllowedTransitions(task).length > 0}
                            onDragStart={(e) => handleDragStart(e, task.id)}
                            onDragOver={(e) => handleDragOverCard(e, task.id)}
                            onDragLeave={(e) => handleDragLeaveCard(e, task.id)}
                            onDrop={(e) => handleDrop(e, col.key, task.id)}
                            onClick={() => handleOpenDetail(task)}
                            className="ta-card"
                            style={{
                              padding: "14px",
                              borderRadius: "10px",
                              cursor: getAllowedTransitions(task).length > 0 ? "grab" : "default",
                              opacity: isDraggingThis ? 0.4 : 1,
                              border: isDragOverThis ? "2px solid var(--primary)" : "1px solid var(--border)",
                              boxShadow: isDraggingThis
                                ? "none"
                                : isDragOverThis
                                ? "0 4px 12px rgba(99, 102, 241, 0.2)"
                                : "0 2px 4px rgba(0,0,0,0.03)",
                              transition: "transform 0.15s ease, box-shadow 0.15s ease, border 0.15s ease",
                            }}
                          >
                            {/* Card Top Header */}
                            <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: "8px" }}>
                              <div style={{ fontSize: "0.88rem", fontWeight: 700, color: "var(--text-dark)", lineHeight: 1.3 }}>
                                {task.title}
                              </div>
                              <div style={{ display: "flex", alignItems: "center", gap: "4px" }}>
                                <button
                                  onClick={(e) => handleOpenEdit(task, e)}
                                  title="Chỉnh sửa task"
                                  style={{ border: "none", background: "transparent", color: "var(--text-muted)", cursor: "pointer", padding: "2px" }}
                                >
                                  <Edit2 size={13} />
                                </button>
                                <button
                                  onClick={(e) => handleOpenDelete(task, e)}
                                  title="Xóa task"
                                  style={{ border: "none", background: "transparent", color: "#EF4444", cursor: "pointer", padding: "2px" }}
                                >
                                  <Trash2 size={13} />
                                </button>
                              </div>
                            </div>

                            {/* Description preview */}
                            {task.description && (
                              <p style={{ fontSize: "0.78rem", color: "var(--text-muted)", marginTop: "6px", marginBottom: "10px", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
                                {task.description}
                              </p>
                            )}

                            {/* Assignee / AI Badge */}
                            <div style={{ display: "flex", alignItems: "center", gap: "6px", marginTop: "10px" }}>
                              {task.ai_agent ? (
                                <span style={{ fontSize: "0.75rem", padding: "2px 8px", borderRadius: "6px", background: "#F3E8FF", color: "#7E22CE", fontWeight: 600, display: "inline-flex", alignItems: "center", gap: "4px" }}>
                                  <span>{task.ai_agent.emoji}</span> {task.ai_agent.name}
                                </span>
                              ) : task.assignee ? (
                                <span style={{ fontSize: "0.75rem", padding: "2px 8px", borderRadius: "6px", background: "#DBEAFE", color: "#1E40AF", fontWeight: 600, display: "inline-flex", alignItems: "center", gap: "4px" }}>
                                  <UserIcon size={12} /> {task.assignee.name}
                                </span>
                              ) : (
                                <span style={{ fontSize: "0.72rem", color: "var(--text-light)", fontStyle: "italic" }}>
                                  Chưa gán
                                </span>
                              )}
                            </div>

                            {/* Priority & Status Controls */}
                            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginTop: "12px", paddingTop: "8px", borderTop: "1px solid #F1F5F9" }}>
                              <span className={`ta-badge ${task.priority === 'URGENT' ? 'ta-badge-danger' : task.priority === 'HIGH' ? 'ta-badge-warning' : 'ta-badge-info'}`}>
                                {task.priority}
                              </span>

                              <select
                                value={task.status}
                                onChange={(e) => {
                                  e.stopPropagation();
                                  handleUpdateStatus(task.id, e.target.value);
                                }}
                                onClick={(e) => e.stopPropagation()}
                                style={{ fontSize: "0.72rem", padding: "2px 6px", borderRadius: "4px", border: "1px solid var(--border)", background: "#FFF" }}
                              >
                                {getStatusOptions(task).map((status) => (
                                  <option key={status} value={status}>{TASK_STATUS_LABELS[status] ?? status}</option>
                                ))}
                              </select>
                            </div>
                          </div>
                        );
                      })}

                      {colTasks.length === 0 && (
                        <div style={{ textAlign: "center", padding: "30px 10px", color: "var(--text-light)", fontSize: "0.8rem", border: "1px dashed var(--border)", borderRadius: "8px" }}>
                          Kéo thả task vào đây
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {/* ── View 2: TABLE VIEW ── */}
          {viewMode === "table" && (
            <div className="ta-card" style={{ overflow: "hidden", borderRadius: "12px" }}>
              <table className="ta-table">
                <thead>
                  <tr>
                    <th>Tiêu đề Task</th>
                    <th>Người Tạo</th>
                    <th>Phụ Trách / AI</th>
                    <th>Độ Ưu Tiên</th>
                    <th>Trạng Thái</th>
                    <th>Hạn Chót</th>
                    <th style={{ textAlign: "right" }}>Thao Tác</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredTasks.map((t) => (
                    <tr key={t.id} onClick={() => handleOpenDetail(t)} style={{ cursor: "pointer" }}>
                      <td style={{ fontWeight: 700, color: "var(--text-dark)" }}>{t.title}</td>
                      <td>{t.creator?.name || "User"}</td>
                      <td>
                        {t.ai_agent ? (
                          <span>{t.ai_agent.emoji} {t.ai_agent.name}</span>
                        ) : t.assignee ? (
                          <span>👤 {t.assignee.name}</span>
                        ) : (
                          <span style={{ color: "var(--text-light)" }}>Chưa gán</span>
                        )}
                      </td>
                      <td>
                        <span className={`ta-badge ${t.priority === 'URGENT' ? 'ta-badge-danger' : t.priority === 'HIGH' ? 'ta-badge-warning' : 'ta-badge-info'}`}>
                          {t.priority}
                        </span>
                      </td>
                      <td onClick={(e) => e.stopPropagation()}>
                        <select
                          value={t.status}
                          onChange={(e) => handleUpdateStatus(t.id, e.target.value)}
                          style={{ fontSize: "0.78rem", padding: "4px 8px", borderRadius: "6px", border: "1px solid var(--border)" }}
                        >
                          {getStatusOptions(t).map((status) => (
                            <option key={status} value={status}>{TASK_STATUS_LABELS[status] ?? status}</option>
                          ))}
                        </select>
                      </td>
                      <td style={{ color: "var(--text-muted)", fontSize: "0.8rem" }}>
                        {t.due_date ? new Date(t.due_date).toLocaleDateString("vi-VN") : "-"}
                      </td>
                      <td style={{ textAlign: "right" }} onClick={(e) => e.stopPropagation()}>
                        <button onClick={(e) => handleOpenEdit(t, e)} style={{ border: "none", background: "none", color: "var(--primary)", cursor: "pointer", marginRight: "10px" }}>
                          <Edit2 size={15} />
                        </button>
                        <button onClick={(e) => handleOpenDelete(t, e)} style={{ border: "none", background: "none", color: "#EF4444", cursor: "pointer" }}>
                          <Trash2 size={15} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* ── View 3: LIST VIEW ── */}
          {viewMode === "list" && (
            <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
              {filteredTasks.map((t) => (
                <div
                  key={t.id}
                  onClick={() => handleOpenDetail(t)}
                  className="ta-card"
                  style={{ padding: "16px 20px", borderRadius: "10px", display: "flex", alignItems: "center", justifyContent: "space-between", cursor: "pointer" }}
                >
                  <div style={{ flex: 1 }}>
                    <div style={{ fontWeight: 700, fontSize: "0.95rem", color: "var(--text-dark)" }}>{t.title}</div>
                    {t.description && <p style={{ fontSize: "0.82rem", color: "var(--text-muted)", marginTop: "2px" }}>{t.description}</p>}
                    <div style={{ fontSize: "0.78rem", color: "var(--text-light)", marginTop: "6px" }}>
                      Tạo bởi {t.creator?.name || 'Admin'} · Gán: {t.ai_agent ? `${t.ai_agent.emoji} ${t.ai_agent.name}` : t.assignee?.name || 'Chưa gán'}
                    </div>
                  </div>

                  <div style={{ display: "flex", alignItems: "center", gap: "12px" }} onClick={(e) => e.stopPropagation()}>
                    <span className="ta-badge ta-badge-info">{t.status}</span>
                    <button onClick={(e) => handleOpenEdit(t, e)} className="ta-btn" style={{ padding: "6px" }}><Edit2 size={14} /></button>
                    <button onClick={(e) => handleOpenDelete(t, e)} className="ta-btn" style={{ padding: "6px", color: "#EF4444" }}><Trash2 size={14} /></button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </main>
      </div>

      {/* ── Modal 1: Create Task ── */}
      {showCreateModal && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 999 }}>
          <div className="ta-card" style={{ width: "520px", padding: "26px", borderRadius: "16px", background: "#FFF" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "20px" }}>
              <h3 style={{ fontSize: "1.15rem", fontWeight: 700, color: "var(--text-dark)" }}>Tạo Task Công Việc Mới</h3>
              <button onClick={() => setShowCreateModal(false)} style={{ background: "none", border: "none", cursor: "pointer" }}><X size={18} /></button>
            </div>

            <form onSubmit={handleCreateTask} style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
              <div>
                <label style={{ fontSize: "0.8rem", fontWeight: 600, color: "var(--text-dark)", marginBottom: "4px", display: "block" }}>Tiêu Đề Task *</label>
                <input type="text" className="ta-input" placeholder="e.g. Kiểm tra hợp đồng dịch vụ đối tác..." value={formTitle} onChange={(e) => setFormTitle(e.target.value)} required />
              </div>

              <div>
                <label style={{ fontSize: "0.8rem", fontWeight: 600, color: "var(--text-dark)", marginBottom: "4px", display: "block" }}>Mô Tả Chi Tiết Nhiệm Vụ</label>
                <textarea className="ta-input" rows={3} placeholder="Chi tiết các yêu cầu..." value={formDesc} onChange={(e) => setFormDesc(e.target.value)} />
              </div>

              <div className="stack-mobile" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
                <div>
                  <label style={{ fontSize: "0.8rem", fontWeight: 600, color: "var(--text-dark)", marginBottom: "4px", display: "block" }}>Mức Độ Ưu Tiên</label>
                  <select className="ta-input" value={formPriority} onChange={(e) => setFormPriority(e.target.value)}>
                    <option value="LOW">LOW (Thấp)</option>
                    <option value="MEDIUM">MEDIUM (Vừa)</option>
                    <option value="HIGH">HIGH (Cao)</option>
                    <option value="URGENT">URGENT (Khẩn cấp)</option>
                  </select>
                </div>

                <div>
                  <label style={{ fontSize: "0.8rem", fontWeight: 600, color: "var(--text-dark)", marginBottom: "4px", display: "block" }}>Trạng Thái Ban Đầu</label>
                  <select className="ta-input" value={formStatus} onChange={(e) => setFormStatus(e.target.value)}>
                    <option value="DRAFT">DRAFT (Nháp)</option>
                    <option value="PENDING">PENDING (Chờ xử lý)</option>
                  </select>
                </div>
              </div>

              {/* Assignee Selection */}
              <div className="stack-mobile" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
                <div>
                  <label style={{ fontSize: "0.8rem", fontWeight: 600, color: "var(--text-dark)", marginBottom: "4px", display: "block" }}>Đối Tượng Phụ Trách</label>
                  <select className="ta-input" value={formAssigneeType} onChange={(e) => { setFormAssigneeType(e.target.value as "NONE" | "USER" | "AGENT"); setFormAssigneeId(""); }}>
                    <option value="NONE">Chưa gán</option>
                    <option value="AGENT">🤖 AI Agent Employee</option>
                    <option value="USER">👤 Nhân viên (User)</option>
                  </select>
                </div>

                {formAssigneeType === "AGENT" && (
                  <div>
                    <label style={{ fontSize: "0.8rem", fontWeight: 600, color: "var(--text-dark)", marginBottom: "4px", display: "block" }}>Chọn AI Agent</label>
                    <select className="ta-input" value={formAssigneeId} onChange={(e) => setFormAssigneeId(e.target.value)}>
                      <option value="">-- Chọn AI Agent --</option>
                      {agents.map((a) => (
                        <option key={a.id} value={a.id}>{a.avatar_emoji} {a.name} ({a.role_code})</option>
                      ))}
                    </select>
                  </div>
                )}

                {formAssigneeType === "USER" && (
                  <div>
                    <label style={{ fontSize: "0.8rem", fontWeight: 600, color: "var(--text-dark)", marginBottom: "4px", display: "block" }}>Chọn Nhân Viên</label>
                    <select className="ta-input" value={formAssigneeId} onChange={(e) => setFormAssigneeId(e.target.value)}>
                      <option value="">-- Chọn Nhân viên --</option>
                      {usersList.map((u) => (
                        <option key={u.id} value={u.id}>{u.full_name} ({u.role})</option>
                      ))}
                    </select>
                  </div>
                )}
              </div>

              <div>
                <label style={{ fontSize: "0.8rem", fontWeight: 600, color: "var(--text-dark)", marginBottom: "4px", display: "block" }}>Hạn Chót Hoàn Thành (Due Date)</label>
                <input type="date" className="ta-input" value={formDueDate} onChange={(e) => setFormDueDate(e.target.value)} />
              </div>

              <button type="submit" className="ta-btn ta-btn-primary" style={{ marginTop: "10px", width: "100%", justifyContent: "center" }}>
                <Plus size={16} /> Tạo Công Việc Mới
              </button>
            </form>
          </div>
        </div>
      )}

      {/* ── Modal 2: Edit Task ── */}
      {showEditModal && activeTask && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 999 }}>
          <div className="ta-card" style={{ width: "520px", padding: "26px", borderRadius: "16px", background: "#FFF" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "20px" }}>
              <h3 style={{ fontSize: "1.15rem", fontWeight: 700, color: "var(--text-dark)" }}>Chỉnh Sửa Task</h3>
              <button onClick={() => setShowEditModal(false)} style={{ background: "none", border: "none", cursor: "pointer" }}><X size={18} /></button>
            </div>

            <form onSubmit={handleEditTask} style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
              <div>
                <label style={{ fontSize: "0.8rem", fontWeight: 600, color: "var(--text-dark)", marginBottom: "4px", display: "block" }}>Tiêu Đề Task *</label>
                <input type="text" className="ta-input" value={formTitle} onChange={(e) => setFormTitle(e.target.value)} required />
              </div>

              <div>
                <label style={{ fontSize: "0.8rem", fontWeight: 600, color: "var(--text-dark)", marginBottom: "4px", display: "block" }}>Mô Tả Chi Tiết</label>
                <textarea className="ta-input" rows={3} value={formDesc} onChange={(e) => setFormDesc(e.target.value)} />
              </div>

              <div className="stack-mobile" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
                <div>
                  <label style={{ fontSize: "0.8rem", fontWeight: 600, color: "var(--text-dark)", marginBottom: "4px", display: "block" }}>Mức Độ Ưu Tiên</label>
                  <select className="ta-input" value={formPriority} onChange={(e) => setFormPriority(e.target.value)}>
                    <option value="LOW">LOW</option>
                    <option value="MEDIUM">MEDIUM</option>
                    <option value="HIGH">HIGH</option>
                    <option value="URGENT">URGENT</option>
                  </select>
                </div>

                <div>
                  <label style={{ fontSize: "0.8rem", fontWeight: 600, color: "var(--text-dark)", marginBottom: "4px", display: "block" }}>Trạng Thái Task</label>
                  <select className="ta-input" value={formStatus} onChange={(e) => setFormStatus(e.target.value)}>
                    {getStatusOptions(activeTask).map((status) => (
                      <option key={status} value={status}>{status}</option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="stack-mobile" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
                <div>
                  <label style={{ fontSize: "0.8rem", fontWeight: 600, color: "var(--text-dark)", marginBottom: "4px", display: "block" }}>Gán Phụ Trách</label>
                  <select className="ta-input" value={formAssigneeType} onChange={(e) => { setFormAssigneeType(e.target.value as "NONE" | "USER" | "AGENT"); setFormAssigneeId(""); }}>
                    <option value="NONE">Chưa gán</option>
                    <option value="AGENT">🤖 AI Agent</option>
                    <option value="USER">👤 Nhân viên</option>
                  </select>
                </div>

                {formAssigneeType === "AGENT" && (
                  <div>
                    <label style={{ fontSize: "0.8rem", fontWeight: 600, color: "var(--text-dark)", marginBottom: "4px", display: "block" }}>Chọn AI Agent</label>
                    <select className="ta-input" value={formAssigneeId} onChange={(e) => setFormAssigneeId(e.target.value)}>
                      <option value="">-- Chọn AI Agent --</option>
                      {agents.map((a) => (
                        <option key={a.id} value={a.id}>{a.avatar_emoji} {a.name}</option>
                      ))}
                    </select>
                  </div>
                )}

                {formAssigneeType === "USER" && (
                  <div>
                    <label style={{ fontSize: "0.8rem", fontWeight: 600, color: "var(--text-dark)", marginBottom: "4px", display: "block" }}>Chọn Nhân Viên</label>
                    <select className="ta-input" value={formAssigneeId} onChange={(e) => setFormAssigneeId(e.target.value)}>
                      <option value="">-- Chọn Nhân viên --</option>
                      {usersList.map((u) => (
                        <option key={u.id} value={u.id}>{u.full_name}</option>
                      ))}
                    </select>
                  </div>
                )}
              </div>

              <div>
                <label style={{ fontSize: "0.8rem", fontWeight: 600, color: "var(--text-dark)", marginBottom: "4px", display: "block" }}>Hạn Chót (Due Date)</label>
                <input type="date" className="ta-input" value={formDueDate} onChange={(e) => setFormDueDate(e.target.value)} />
              </div>

              <button type="submit" className="ta-btn ta-btn-primary" style={{ marginTop: "10px", width: "100%", justifyContent: "center" }}>
                Lưu Thay Đổi
              </button>
            </form>
          </div>
        </div>
      )}

      {/* ── Modal 3: Delete Confirmation ── */}
      {showDeleteModal && activeTask && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 999 }}>
          <div className="ta-card" style={{ width: "400px", padding: "24px", borderRadius: "16px", background: "#FFF", textAlign: "center" }}>
            <div style={{ width: "48px", height: "48px", borderRadius: "50%", background: "#FEE2E2", color: "#EF4444", display: "flex", alignItems: "center", justifyContent: "center", margin: "0 auto 16px auto" }}>
              <AlertTriangle size={24} />
            </div>
            <h3 style={{ fontSize: "1.1rem", fontWeight: 700, color: "var(--text-dark)", marginBottom: "8px" }}>
              Xác nhận xóa Task này?
            </h3>
            <p style={{ fontSize: "0.85rem", color: "var(--text-muted)", marginBottom: "20px" }}>
              Task <strong>&quot;{activeTask.title}&quot;</strong> sẽ bị xóa vĩnh viễn khỏi Database. Thao tác này không thể hoàn tác!
            </p>

            <div className="stack-mobile" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
              <button onClick={() => setShowDeleteModal(false)} className="ta-btn" style={{ justifyContent: "center" }}>
                Hủy bỏ
              </button>
              <button onClick={handleDeleteTask} className="ta-btn" style={{ background: "#EF4444", color: "#FFF", justifyContent: "center" }}>
                Xóa Task
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Modal 4: Task Detail & Comments Timeline ── */}
      {showDetailModal && activeTask && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 999 }}>
          <div className="ta-card" style={{ width: "640px", maxHeight: "85vh", display: "flex", flexDirection: "column", padding: "26px", borderRadius: "16px", background: "#FFF" }}>
            {/* Header */}
            <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: "16px" }}>
              <div>
                <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "6px" }}>
                  <span className="ta-badge ta-badge-info">{activeTask.status}</span>
                  <span className="ta-badge ta-badge-warning">{activeTask.priority}</span>
                </div>
                <h2 style={{ fontSize: "1.25rem", fontWeight: 800, color: "var(--text-dark)" }}>
                  {activeTask.title}
                </h2>
              </div>
              <button onClick={() => { setShowDetailModal(false); setTaskDetails(null); }} style={{ background: "none", border: "none", cursor: "pointer" }}><X size={20} /></button>
            </div>

            {/* Body Scrollable */}
            <div style={{ flex: 1, overflowY: "auto", paddingRight: "6px", display: "flex", flexDirection: "column", gap: "16px" }}>
              {/* Meta information */}
              <div style={{ background: "#F8FAFC", padding: "14px", borderRadius: "10px", display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px", fontSize: "0.82rem" }}>
                <div>
                  <span style={{ color: "var(--text-light)" }}>Người tạo: </span>
                  <strong style={{ color: "var(--text-dark)" }}>{activeTask.creator?.name || "Admin"}</strong>
                </div>
                <div>
                  <span style={{ color: "var(--text-light)" }}>Phụ trách: </span>
                  <strong style={{ color: "var(--text-dark)" }}>
                    {activeTask.ai_agent ? `${activeTask.ai_agent.emoji} ${activeTask.ai_agent.name}` : activeTask.assignee?.name || "Chưa gán"}
                  </strong>
                </div>
                <div>
                  <span style={{ color: "var(--text-light)" }}>Ngày tạo: </span>
                  <strong>{activeTask.created_at ? new Date(activeTask.created_at).toLocaleString("vi-VN") : "-"}</strong>
                </div>
                <div>
                  <span style={{ color: "var(--text-light)" }}>Hạn chót: </span>
                  <strong>{activeTask.due_date ? new Date(activeTask.due_date).toLocaleDateString("vi-VN") : "Không có"}</strong>
                </div>
              </div>

              {/* Description */}
              <div>
                <h4 style={{ fontSize: "0.85rem", fontWeight: 700, color: "var(--text-dark)", marginBottom: "6px" }}>Mô tả công việc:</h4>
                <p style={{ fontSize: "0.85rem", color: "var(--text-muted)", background: "#FFF", padding: "12px", borderRadius: "8px", border: "1px solid var(--border)", whiteSpace: "pre-wrap" }}>
                  {activeTask.description || "Chưa có mô tả chi tiết."}
                </p>
              </div>

              {/* Comments Section */}
              <div style={{ marginTop: "10px" }}>
                <h4 style={{ fontSize: "0.88rem", fontWeight: 700, color: "var(--text-dark)", marginBottom: "10px", display: "flex", alignItems: "center", gap: "6px" }}>
                  <MessageSquare size={16} /> Bình Luận ({taskDetails?.comments?.length || 0})
                </h4>

                <div style={{ display: "flex", flexDirection: "column", gap: "10px", maxHeight: "200px", overflowY: "auto", marginBottom: "14px", paddingRight: "4px" }}>
                  {taskDetails?.comments?.map((c: TaskCommentItem) => (
                    <div key={c.id} style={{ background: "#F1F5F9", padding: "10px 14px", borderRadius: "10px" }}>
                      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "4px" }}>
                        <span style={{ fontWeight: 700, fontSize: "0.8rem", color: "var(--text-dark)" }}>{c.user_name}</span>
                        <span style={{ fontSize: "0.72rem", color: "var(--text-light)" }}>{new Date(c.created_at).toLocaleTimeString("vi-VN")}</span>
                      </div>
                      <p style={{ fontSize: "0.82rem", color: "var(--text-muted)" }}>{c.content}</p>
                    </div>
                  ))}

                  {(!taskDetails?.comments || taskDetails.comments.length === 0) && (
                    <p style={{ fontSize: "0.8rem", color: "var(--text-light)", fontStyle: "italic" }}>Chưa có bình luận nào.</p>
                  )}
                </div>

                {/* Add Comment Form */}
                <form onSubmit={handleAddComment} style={{ display: "flex", gap: "8px" }}>
                  <input
                    type="text"
                    className="ta-input"
                    placeholder="Viết bình luận hoặc chỉ đạo công việc..."
                    value={commentInput}
                    onChange={(e) => setCommentInput(e.target.value)}
                    style={{ flex: 1 }}
                  />
                  <button type="submit" className="ta-btn ta-btn-primary" disabled={submittingComment}>
                    <Send size={15} /> Gửi
                  </button>
                </form>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
