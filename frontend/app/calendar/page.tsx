"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/store/useAuthStore";
import Sidebar from "@/components/Sidebar";
import api from "@/lib/api";
import {
  Calendar as CalendarIcon,
  ChevronLeft,
  ChevronRight,
  Plus,
  Filter,
  Search,
  User as UserIcon,
  Bot,
  Clock,
  CheckCircle2,
  AlertTriangle,
  X,
  Tag,
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

const MONTH_NAMES = [
  "Tháng 1", "Tháng 2", "Tháng 3", "Tháng 4", "Tháng 5", "Tháng 6",
  "Tháng 7", "Tháng 8", "Tháng 9", "Tháng 10", "Tháng 11", "Tháng 12"
];

const DAYS_OF_WEEK = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"];

export default function CalendarPage() {
  const router = useRouter();
  const { isAuthenticated, hasHydrated } = useAuthStore();

  const [currentDate, setCurrentDate] = useState(new Date());
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [agents, setAgents] = useState<AgentItem[]>([]);
  const [usersList, setUsersList] = useState<UserMember[]>([]);
  const [loading, setLoading] = useState(true);

  // Filters
  const [filterPriority, setFilterPriority] = useState<string>("ALL");
  const [filterStatus, setFilterStatus] = useState<string>("ALL");
  const [searchQuery, setSearchQuery] = useState("");

  // Modals
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showDetailModal, setShowDetailModal] = useState(false);
  const [selectedTask, setSelectedTask] = useState<TaskItem | null>(null);

  // Form State for Quick Task Creation
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
      const [tasksRes, agentsRes, usersRes, hrEventsRes] = await Promise.allSettled([
        api.get("/api/v1/tasks"),
        api.get("/api/v1/agents"),
        api.get("/api/v1/users-mgmt"),
        api.get("/api/v1/hr/calendar-events"),
      ]);

      if (tasksRes.status === "fulfilled") {
        const taskItems: TaskItem[] = tasksRes.value.data;
        const hrItems: TaskItem[] = hrEventsRes.status === "fulfilled"
          ? hrEventsRes.value.data.map((event: { id: string; title: string; start_at: string; user: { id: string; name: string }; sync_status: string }) => ({
              id: `hr-${event.id}`,
              title: event.title,
              description: `Sự kiện nhân sự · Trạng thái đồng bộ: ${event.sync_status}`,
              creator: event.user,
              assignee: event.user,
              ai_agent: null,
              priority: "MEDIUM",
              due_date: event.start_at,
              status: "COMPLETED",
              created_at: event.start_at,
            }))
          : [];
        setTasks([...taskItems, ...hrItems]);
      }
      if (agentsRes.status === "fulfilled") setAgents(agentsRes.value.data);
      if (usersRes.status === "fulfilled") setUsersList(usersRes.value.data);
    } catch (err) {
      console.error("Failed to fetch calendar data:", err);
    } finally {
      setLoading(false);
    }
  }

  // Calendar Date Calculations
  const year = currentDate.getFullYear();
  const month = currentDate.getMonth();

  const firstDayOfMonth = new Date(year, month, 1);
  const lastDayOfMonth = new Date(year, month + 1, 0);
  const daysInMonth = lastDayOfMonth.getDate();

  // Convert Sunday=0 to T2=0 ... CN=6
  let startingDayOfWeek = firstDayOfMonth.getDay() - 1;
  if (startingDayOfWeek === -1) startingDayOfWeek = 6;

  const prevMonthLastDay = new Date(year, month, 0).getDate();

  // Navigation Handlers
  const handlePrevMonth = () => {
    setCurrentDate(new Date(year, month - 1, 1));
  };

  const handleNextMonth = () => {
    setCurrentDate(new Date(year, month + 1, 1));
  };

  const handleToday = () => {
    setCurrentDate(new Date());
  };

  // Open Create Modal pre-filled with clicked date
  const handleDayClick = (dayNum: number) => {
    const clickedDate = new Date(year, month, dayNum);
    const dateStr = clickedDate.toISOString().substring(0, 10);
    setFormDueDate(dateStr);
    setFormTitle("");
    setFormDesc("");
    setFormPriority("MEDIUM");
    setFormStatus("PENDING");
    setFormAssigneeType("NONE");
    setFormAssigneeId("");
    setShowCreateModal(true);
  };

  // Create Task Submit
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
      alert("Không thể tạo task!");
    }
  };

  // Filter Tasks
  const filteredTasks = tasks.filter((t) => {
    const matchesSearch =
      t.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
      (t.description && t.description.toLowerCase().includes(searchQuery.toLowerCase()));
    const matchesPriority = filterPriority === "ALL" || t.priority === filterPriority;
    const matchesStatus = filterStatus === "ALL" || t.status === filterStatus;
    return matchesSearch && matchesPriority && matchesStatus;
  });

  // Get tasks for a specific calendar day
  const getTasksForDay = (dayNum: number) => {
    const targetDateStr = `${year}-${String(month + 1).padStart(2, "0")}-${String(dayNum).padStart(2, "0")}`;
    return filteredTasks.filter((t) => {
      if (t.due_date) {
        return t.due_date.substring(0, 10) === targetDateStr;
      }
      return t.created_at.substring(0, 10) === targetDateStr;
    });
  };

  const today = new Date();
  const isCurrentMonth = today.getFullYear() === year && today.getMonth() === month;

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
            <span className="breadcrumb-current">Lịch Công Việc (Calendar)</span>
          </div>

          <button className="ta-btn ta-btn-primary" onClick={() => handleDayClick(today.getDate())}>
            <Plus size={16} /> Tạo Task Mới
          </button>
        </header>

        {/* Main Calendar Content */}
        <main className="page-main" style={{ flex: 1, overflowY: "auto", padding: "24px 32px" }}>
          {/* Header Controls & Month Picker */}
          <div style={{ marginBottom: "20px", display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: "16px" }}>
            <div>
              <h1 style={{ fontSize: "1.5rem", fontWeight: 800, color: "var(--text-dark)", display: "flex", alignItems: "center", gap: "10px" }}>
                <CalendarIcon size={26} color="var(--primary)" /> Lịch Theo Dõi Tiến Độ Công Việc
              </h1>
              <p style={{ fontSize: "0.85rem", color: "var(--text-muted)", marginTop: "4px" }}>
                Trực quan hóa hạn chót và lịch làm việc của Nhân viên & AI Agents theo tháng.
              </p>
            </div>

            {/* Month Navigator */}
            <div style={{ display: "flex", alignItems: "center", gap: "10px", background: "#FFF", padding: "6px 12px", borderRadius: "12px", border: "1px solid var(--border)" }}>
              <button
                onClick={handlePrevMonth}
                style={{ border: "none", background: "none", cursor: "pointer", color: "var(--text-dark)", padding: "4px" }}
                title="Tháng trước"
              >
                <ChevronLeft size={20} />
              </button>
              
              <span style={{ fontSize: "1rem", fontWeight: 800, minWidth: "140px", textAlign: "center", color: "var(--text-dark)" }}>
                {MONTH_NAMES[month]} {year}
              </span>

              <button
                onClick={handleNextMonth}
                style={{ border: "none", background: "none", cursor: "pointer", color: "var(--text-dark)", padding: "4px" }}
                title="Tháng sau"
              >
                <ChevronRight size={20} />
              </button>

              <button
                onClick={handleToday}
                style={{
                  fontSize: "0.78rem",
                  fontWeight: 700,
                  padding: "4px 10px",
                  borderRadius: "6px",
                  border: "1px solid var(--primary)",
                  background: "#EEF2FF",
                  color: "var(--primary)",
                  cursor: "pointer",
                  marginLeft: "6px",
                }}
              >
                Hôm Nay
              </button>
            </div>
          </div>

          {/* Search & Filter Bar */}
          <div className="wrap-mobile" style={{ display: "flex", alignItems: "center", gap: "14px", marginBottom: "20px" }}>
            <div style={{ position: "relative", flex: 1, maxWidth: "340px" }}>
              <Search size={16} style={{ position: "absolute", left: "12px", top: "50%", transform: "translateY(-50%)", color: "var(--text-light)" }} />
              <input
                type="text"
                placeholder="Tìm công việc theo tiêu đề..."
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
              <option value="LOW">Thấp (Low)</option>
              <option value="MEDIUM">Vừa (Medium)</option>
              <option value="HIGH">Cao (High)</option>
              <option value="URGENT">Khẩn cấp (Urgent)</option>
            </select>

            <select
              value={filterStatus}
              onChange={(e) => setFilterStatus(e.target.value)}
              className="ta-input"
              style={{ width: "170px", height: "38px", fontSize: "0.85rem" }}
            >
              <option value="ALL">Tất cả trạng thái</option>
              <option value="DRAFT">Nháp (Draft)</option>
              <option value="PENDING">Chờ xử lý</option>
              <option value="RUNNING">Đang chạy</option>
              <option value="WAITING_APPROVAL">Chờ phê duyệt</option>
              <option value="COMPLETED">Hoàn thành</option>
            </select>
          </div>

          {/* Calendar Grid */}
          <div className="ta-card scroll-x-mobile" style={{ padding: "0", borderRadius: "16px", overflow: "hidden" }}>
            {/* Weekdays Header */}
            <div className="calendar-grid" style={{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)", background: "#F8FAFC", borderBottom: "1px solid var(--border)" }}>
              {DAYS_OF_WEEK.map((day, idx) => (
                <div
                  key={day}
                  style={{
                    padding: "12px",
                    textAlign: "center",
                    fontWeight: 700,
                    fontSize: "0.85rem",
                    color: idx >= 5 ? "#EF4444" : "var(--text-dark)",
                    borderRight: idx < 6 ? "1px solid #F1F5F9" : "none",
                  }}
                >
                  {day}
                </div>
              ))}
            </div>

            {/* Days Grid */}
            <div className="calendar-grid" style={{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)", background: "#E2E8F0", gap: "1px" }}>
              {/* Previous Month Padding Days */}
              {Array.from({ length: startingDayOfWeek }).map((_, idx) => {
                const prevDay = prevMonthLastDay - startingDayOfWeek + idx + 1;
                return (
                  <div
                    key={`prev-${idx}`}
                    style={{
                      background: "#FAFAFA",
                      minHeight: "115px",
                      padding: "8px",
                      opacity: 0.4,
                      userSelect: "none",
                    }}
                  >
                    <span style={{ fontSize: "0.8rem", fontWeight: 600, color: "var(--text-light)" }}>
                      {prevDay}
                    </span>
                  </div>
                );
              })}

              {/* Current Month Days */}
              {Array.from({ length: daysInMonth }).map((_, idx) => {
                const dayNum = idx + 1;
                const dayTasks = getTasksForDay(dayNum);
                const isToday = isCurrentMonth && today.getDate() === dayNum;

                return (
                  <div
                    key={`day-${dayNum}`}
                    onClick={() => handleDayClick(dayNum)}
                    style={{
                      background: "#FFFFFF",
                      minHeight: "115px",
                      padding: "8px",
                      cursor: "pointer",
                      display: "flex",
                      flexDirection: "column",
                      transition: "background 0.15s ease",
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = "#F8FAFC")}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "#FFFFFF")}
                  >
                    {/* Day Number Header */}
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "6px" }}>
                      <span
                        style={{
                          fontSize: "0.85rem",
                          fontWeight: isToday ? 800 : 700,
                          width: "24px",
                          height: "24px",
                          borderRadius: "50%",
                          background: isToday ? "var(--primary)" : "transparent",
                          color: isToday ? "#FFFFFF" : "var(--text-dark)",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                        }}
                      >
                        {dayNum}
                      </span>

                      {dayTasks.length > 0 && (
                        <span style={{ fontSize: "0.7rem", fontWeight: 700, color: "var(--text-muted)", background: "#F1F5F9", padding: "1px 6px", borderRadius: "99px" }}>
                          {dayTasks.length} task
                        </span>
                      )}
                    </div>

                    {/* Day Task List */}
                    <div style={{ display: "flex", flexDirection: "column", gap: "4px", overflowY: "auto", flex: 1, maxHeight: "85px" }}>
                      {dayTasks.map((t) => (
                        <div
                          key={t.id}
                          onClick={(e) => {
                            e.stopPropagation();
                            setSelectedTask(t);
                            setShowDetailModal(true);
                          }}
                          style={{
                            padding: "4px 6px",
                            borderRadius: "6px",
                            fontSize: "0.72rem",
                            fontWeight: 600,
                            background:
                              t.priority === "URGENT"
                                ? "#FEE2E2"
                                : t.priority === "HIGH"
                                ? "#FEF3C7"
                                : "#EFF6FF",
                            color:
                              t.priority === "URGENT"
                                ? "#991B1B"
                                : t.priority === "HIGH"
                                ? "#92400E"
                                : "#1E40AF",
                            borderLeft: `3px solid ${
                              t.status === "COMPLETED"
                                ? "#10B981"
                                : t.status === "RUNNING"
                                ? "#8B5CF6"
                                : "var(--primary)"
                            }`,
                            whiteSpace: "nowrap",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            cursor: "pointer",
                          }}
                          title={t.title}
                        >
                          {t.ai_agent && <span>{t.ai_agent.emoji} </span>}
                          {t.title}
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </main>
      </div>

      {/* ── Modal 1: Quick Create Task for Date ── */}
      {showCreateModal && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 999 }}>
          <div className="ta-card" style={{ width: "520px", padding: "26px", borderRadius: "16px", background: "#FFF" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "20px" }}>
              <h3 style={{ fontSize: "1.15rem", fontWeight: 700, color: "var(--text-dark)" }}>Tạo Task Cho Ngày {formDueDate}</h3>
              <button onClick={() => setShowCreateModal(false)} style={{ background: "none", border: "none", cursor: "pointer" }}><X size={18} /></button>
            </div>

            <form onSubmit={handleCreateTask} style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
              <div>
                <label style={{ fontSize: "0.8rem", fontWeight: 600, color: "var(--text-dark)", marginBottom: "4px", display: "block" }}>Tiêu Đề Task *</label>
                <input type="text" className="ta-input" placeholder="e.g. Kiểm tra hợp đồng..." value={formTitle} onChange={(e) => setFormTitle(e.target.value)} required />
              </div>

              <div>
                <label style={{ fontSize: "0.8rem", fontWeight: 600, color: "var(--text-dark)", marginBottom: "4px", display: "block" }}>Mô Tả Chi Tiết</label>
                <textarea className="ta-input" rows={3} placeholder="Nội dung công việc..." value={formDesc} onChange={(e) => setFormDesc(e.target.value)} />
              </div>

              <div className="stack-mobile" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
                <div>
                  <label style={{ fontSize: "0.8rem", fontWeight: 600, color: "var(--text-dark)", marginBottom: "4px", display: "block" }}>Mức Độ Ưu Tiên</label>
                  <select className="ta-input" value={formPriority} onChange={(e) => setFormPriority(e.target.value)}>
                    <option value="LOW">Thấp (Low)</option>
                    <option value="MEDIUM">Vừa (Medium)</option>
                    <option value="HIGH">Cao (High)</option>
                    <option value="URGENT">Khẩn cấp (Urgent)</option>
                  </select>
                </div>

                <div>
                  <label style={{ fontSize: "0.8rem", fontWeight: 600, color: "var(--text-dark)", marginBottom: "4px", display: "block" }}>Hạn Chót (Due Date)</label>
                  <input type="date" className="ta-input" value={formDueDate} onChange={(e) => setFormDueDate(e.target.value)} />
                </div>
              </div>

              {/* Assignee Selection */}
              <div className="stack-mobile" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
                <div>
                  <label style={{ fontSize: "0.8rem", fontWeight: 600, color: "var(--text-dark)", marginBottom: "4px", display: "block" }}>Đối Tượng Phụ Trách</label>
                  <select className="ta-input" value={formAssigneeType} onChange={(e) => { setFormAssigneeType(e.target.value as "NONE" | "USER" | "AGENT"); setFormAssigneeId(""); }}>
                    <option value="NONE">Chưa gán</option>
                    <option value="AGENT">🤖 AI Agent</option>
                    <option value="USER">👤 Nhân viên (User)</option>
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

              <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px", marginTop: "16px" }}>
                <button type="button" className="ta-btn" onClick={() => setShowCreateModal(false)}>Hủy</button>
                <button type="submit" className="ta-btn ta-btn-primary">Tạo Task Lịch</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* ── Modal 2: Task Detail Modal ── */}
      {showDetailModal && selectedTask && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 999 }}>
          <div className="ta-card" style={{ width: "540px", padding: "26px", borderRadius: "16px", background: "#FFF" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "16px" }}>
              <span className={`ta-badge ${selectedTask.priority === 'URGENT' ? 'ta-badge-danger' : selectedTask.priority === 'HIGH' ? 'ta-badge-warning' : 'ta-badge-info'}`}>
                {selectedTask.priority}
              </span>
              <button onClick={() => setShowDetailModal(false)} style={{ background: "none", border: "none", cursor: "pointer" }}><X size={18} /></button>
            </div>

            <h3 style={{ fontSize: "1.2rem", fontWeight: 800, color: "var(--text-dark)", marginBottom: "8px" }}>{selectedTask.title}</h3>
            
            {selectedTask.description && (
              <p style={{ fontSize: "0.88rem", color: "var(--text-muted)", marginBottom: "16px", background: "#F8FAFC", padding: "12px", borderRadius: "8px" }}>
                {selectedTask.description}
              </p>
            )}

            <div className="stack-mobile" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px", fontSize: "0.82rem", color: "var(--text-dark)", marginBottom: "20px" }}>
              <div><strong>Trạng thái:</strong> {selectedTask.status}</div>
              <div><strong>Hạn chót:</strong> {selectedTask.due_date ? new Date(selectedTask.due_date).toLocaleDateString("vi-VN") : "Không có"}</div>
              <div><strong>Người phụ trách:</strong> {selectedTask.ai_agent ? `${selectedTask.ai_agent.emoji} ${selectedTask.ai_agent.name}` : selectedTask.assignee?.name || "Chưa gán"}</div>
              <div><strong>Người tạo:</strong> {selectedTask.creator?.name || "Admin"}</div>
            </div>

            <div style={{ display: "flex", justifyContent: "flex-end" }}>
              <button className="ta-btn ta-btn-primary" onClick={() => setShowDetailModal(false)}>Đóng</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
