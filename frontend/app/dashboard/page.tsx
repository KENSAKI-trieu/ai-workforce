"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/store/useAuthStore";
import Sidebar from "@/components/Sidebar";
import api from "@/lib/api";
import {
  Bot,
  Users,
  MessageSquare,
  TrendingDown,
  TrendingUp,
  Activity,
  Download,
  CalendarDays,
  Search,
  Bell,
  RefreshCw,
  ChevronUp,
  ChevronDown,
  MoreHorizontal,
  Zap,
  CheckCircle2,
  Clock,
  XCircle,
  ArrowUpRight,
  ArrowDownRight,
  FileSpreadsheet,
  FileText,
  ChevronRight,
  Moon,
  Settings,
} from "lucide-react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
  BarChart,
  Bar,
} from "recharts";

interface Agent {
  id: string;
  name: string;
  role_code: string;
  avatar_emoji: string;
  description: string;
  is_active: boolean;
  model_name: string | null;
}

interface ChatbotItem {
  id: string;
  name: string;
  emoji: string;
  dept: string;
  conversations: number;
  accuracy: number;
  status: string;
  role_code?: string;
}

interface EmployeeItem {
  id: string;
  name: string;
  dept: string;
  avatar: string;
  msgs: number;
  pct: number;
}

interface DashboardStats {
  chatbots?: ChatbotItem[];
  top_employees?: EmployeeItem[];
  usage_trend?: Array<{ day: string; ai: number; handoff: number }>;
  monthly_data?: Array<{ month: string; value: number }>;
  kpi?: {
    active_bots?: string;
    total_messages?: number;
    active_employees?: number;
    total_employees?: number;
    handoff_rate?: string;
  };
  monthly_summary?: {
    target?: number;
    completed?: number;
    avg_per_day?: number;
  };
}

interface ChartTooltipPayload {
  dataKey: string;
  color: string;
  name: string;
  value: number;
}

// ─── Mock data ───────────────────────────────────────────────────────────────
const USAGE_TREND_DATA = [
  { day: "T2", ai: 420, handoff: 18 },
  { day: "T3", ai: 380, handoff: 22 },
  { day: "T4", ai: 510, handoff: 15 },
  { day: "T5", ai: 475, handoff: 20 },
  { day: "T6", ai: 620, handoff: 28 },
  { day: "T7", ai: 390, handoff: 12 },
  { day: "CN", ai: 280, handoff: 8 },
];

const MONTHLY_DATA = [
  { month: "T1", value: 120 },
  { month: "T2", value: 280 },
  { month: "T3", value: 180 },
  { month: "T4", value: 310 },
  { month: "T5", value: 160 },
  { month: "T6", value: 200 },
  { month: "T7", value: 270 },
  { month: "T8", value: 95 },
  { month: "T9", value: 220 },
  { month: "T10", value: 380 },
  { month: "T11", value: 280 },
  { month: "T12", value: 130 },
];

const CHATBOTS: ChatbotItem[] = [
  { id: "bot-1", name: "Hỗ Trợ Nhân Sự", emoji: "🧑‍💼", dept: "Human Resources", conversations: 45210, accuracy: 94.2, status: "active" },
  { id: "bot-2", name: "Chăm Sóc Khách Hàng", emoji: "💬", dept: "Customer Support", conversations: 38450, accuracy: 88.5, status: "active" },
  { id: "bot-3", name: "Tư Vấn Bán Hàng", emoji: "📈", dept: "Sales", conversations: 29800, accuracy: 91.3, status: "active" },
  { id: "bot-4", name: "Hỗ Trợ IT", emoji: "💻", dept: "IT Department", conversations: 18920, accuracy: 96.1, status: "training" },
  { id: "bot-5", name: "Tư Vấn Pháp Lý", emoji: "⚖️", dept: "Legal", conversations: 12450, accuracy: 82.7, status: "active" },
  { id: "bot-6", name: "Quản Lý Tài Chính", emoji: "💰", dept: "Finance", conversations: 9870, accuracy: 93.8, status: "inactive" },
];

const TOP_EMPLOYEES: EmployeeItem[] = [
  { id: "emp-1", name: "Nguyễn Văn An", dept: "Sales Support", avatar: "NA", msgs: 1450, pct: 92 },
  { id: "emp-2", name: "Trần Thị Bích", dept: "Customer Care", avatar: "TB", msgs: 1280, pct: 81 },
  { id: "emp-3", name: "Lê Minh Cường", dept: "HR Department", avatar: "LC", msgs: 1100, pct: 70 },
  { id: "emp-4", name: "Phạm Thu Hà", dept: "IT Support", avatar: "PH", msgs: 940, pct: 60 },
  { id: "emp-5", name: "Hoàng Đức Nam", dept: "Finance", avatar: "HN", msgs: 780, pct: 49 },
];

const AVATAR_COLORS = ["#3C50E0", "#8b5cf6", "#ec4899", "#f59e0b", "#10b981"];

// ─── Sparkline mini-chart ──────────────────────────────────────────────────
function Sparkline({ data, color }: { data: number[]; color: string }) {
  const max = Math.max(...data);
  const min = Math.min(...data);
  const width = 80;
  const height = 36;
  const pts = data
    .map((v, i) => {
      const x = (i / (data.length - 1)) * width;
      const y = height - ((v - min) / (max - min || 1)) * (height - 4) - 2;
      return `${x},${y}`;
    })
    .join(" ");

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`}>
      <polyline
        fill="none"
        stroke={color}
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        points={pts}
      />
    </svg>
  );
}

// ─── Custom Tooltip ──────────────────────────────────────────────────────────
const CustomTooltip = ({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: ChartTooltipPayload[];
  label?: string;
}) => {
  if (active && payload && payload.length) {
    return (
      <div
        style={{
          background: "#fff",
          border: "1px solid var(--border)",
          borderRadius: "10px",
          padding: "10px 14px",
          boxShadow: "var(--shadow-md)",
          fontSize: "0.82rem",
        }}
      >
        <p style={{ fontWeight: 700, color: "var(--text-dark)", marginBottom: "6px" }}>
          {label}
        </p>
        {payload.map((p) => (
          <p key={p.dataKey} style={{ color: p.color, marginBottom: "2px" }}>
            {p.name === "ai" ? "AI tự động" : "Handoff"}:{" "}
            <span style={{ fontWeight: 700 }}>{p.value}</span>
          </p>
        ))}
      </div>
    );
  }
  return null;
};

// ─── Status Badge ─────────────────────────────────────────────────────────────
function StatusBadge({ status }: { status: string }) {
  const cfg = {
    active: { label: "Active", cls: "ta-badge ta-badge-success" },
    training: { label: "Training", cls: "ta-badge ta-badge-warning" },
    inactive: { label: "Inactive", cls: "ta-badge ta-badge-neutral" },
  }[status] ?? { label: status, cls: "ta-badge ta-badge-neutral" };

  return <span className={cfg.cls}>{cfg.label}</span>;
}

// ─── Main Page ────────────────────────────────────────────────────────────────
export default function DashboardPage() {
  const router = useRouter();
  const { user, isAuthenticated, hasHydrated } = useAuthStore();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [dashboardStats, setDashboardStats] = useState<DashboardStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<"day" | "week" | "month">("week");
  const [exportingExcel, setExportingExcel] = useState(false);
  const [exportingPdf, setExportingPdf] = useState(false);
  const [navSearch, setNavSearch] = useState("");
  const [isSearchFocused, setIsSearchFocused] = useState(false);

  const chatbotsList = (dashboardStats?.chatbots || CHATBOTS).filter((bot) =>
    bot.name.toLowerCase().includes(navSearch.toLowerCase()) ||
    bot.dept.toLowerCase().includes(navSearch.toLowerCase()) ||
    (bot.role_code && bot.role_code.toLowerCase().includes(navSearch.toLowerCase()))
  );

  const topEmployeesList = (dashboardStats?.top_employees || TOP_EMPLOYEES).filter((emp) =>
    emp.name.toLowerCase().includes(navSearch.toLowerCase()) ||
    emp.dept.toLowerCase().includes(navSearch.toLowerCase())
  );

  const currentHour = new Date().getHours();
  const greeting =
    currentHour < 12 ? "Chào buổi sáng" : currentHour < 18 ? "Chào buổi chiều" : "Chào buổi tối";

  useEffect(() => {
    const hasToken = typeof window !== "undefined" && Boolean(localStorage.getItem("access_token"));
    if (!isAuthenticated && !hasToken) {
      router.push("/login");
      return;
    }
    const timer = window.setTimeout(() => {
      void fetchAgents();
      void fetchDashboardStats();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [isAuthenticated, activeTab]);

  async function fetchAgents() {
    try {
      setLoading(true);
      const { data } = await api.get("/api/v1/agents");
      setAgents(data);
    } catch (err) {
      console.error("Failed to fetch agents:", err);
    } finally {
      setLoading(false);
    }
  }

  async function fetchDashboardStats() {
    try {
      setLoading(true);
      const { data } = await api.get("/api/v1/dashboard/stats", {
        params: { period: activeTab },
      });
      setDashboardStats(data);
    } catch (err) {
      console.error("Failed to fetch dashboard stats:", err);
    } finally {
      setLoading(false);
    }
  }

  const handleExportExcel = async () => {
    setExportingExcel(true);
    try {
      const response = await api.get("/api/v1/dashboard/reports/export/excel", {
        responseType: "blob",
        params: { period: activeTab },
      });
      const contentType = String(response.headers["content-type"] || "");
      const isXlsx = contentType.includes("openxmlformats") || contentType.includes("sheet");
      const ext = isXlsx ? "xlsx" : "csv";
      
      const blob = new Blob([response.data], { type: contentType || "application/octet-stream" });
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute("download", `chatbot-report-${activeTab}.${ext}`);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      console.error("Export Excel failed:", err);
      alert("Xuất Excel thất bại. Vui lòng thử lại.");
    } finally {
      setExportingExcel(false);
    }
  };

  const handleExportPdf = async () => {
    setExportingPdf(true);
    try {
      const response = await api.get("/api/v1/dashboard/reports/export/pdf", {
        responseType: "blob",
        params: { period: activeTab },
      });
      const contentType = String(response.headers["content-type"] || "");
      const ext = contentType.includes("pdf") ? "pdf" : "txt";
      
      const blob = new Blob([response.data], { type: contentType || "application/pdf" });
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute("download", `chatbot-report-${activeTab}.${ext}`);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      console.error("Export PDF failed:", err);
      alert("Xuất PDF thất bại. Vui lòng thử lại.");
    } finally {
      setExportingPdf(false);
    }
  };

  const agentStatuses = Object.fromEntries(agents.map((a) => [a.role_code, a.is_active]));
  // isAuthenticated is read from localStorage, which the server render cannot see.
  if (!hasHydrated || !isAuthenticated) return null;

  const kpiCards = [
    {
      id: "kpi-active-bots",
      label: "Active Chatbots",
      value: dashboardStats?.kpi?.active_bots || `${agents.filter((a) => a.is_active).length} / ${agents.length}`,
      sub: "Đang hoạt động từ DB",
      icon: Bot,
      color: "#3C50E0",
      iconBg: "#EEF2FF",
      spark: [5, 6, 6, 7, 7, 7, 7],
      trend: "+100%",
      up: true,
    },
    {
      id: "kpi-total-msgs",
      label: "Total Messages",
      value: (dashboardStats?.kpi?.total_messages ?? 0).toLocaleString(),
      sub: "Tương tác thực từ DB",
      icon: MessageSquare,
      color: "#10B981",
      iconBg: "#D1FAE5",
      spark: [10, 25, 40, 60, 80, 100, 120],
      trend: "+15%",
      up: true,
    },
    {
      id: "kpi-active-emp",
      label: "Active Employees",
      value: (dashboardStats?.kpi?.active_employees ?? 0).toString(),
      sub: `${dashboardStats?.kpi?.total_employees ?? 0} nhân sự từ DB`,
      icon: Users,
      color: "#3B82F6",
      iconBg: "#DBEAFE",
      spark: [10, 20, 30, 35, 38, 40, 41],
      trend: "+100%",
      up: true,
    },
    {
      id: "kpi-handoff",
      label: "Handoff Rate",
      value: dashboardStats?.kpi?.handoff_rate || "0.0%",
      sub: "Duyệt quy trình",
      icon: TrendingDown,
      color: "#F59E0B",
      iconBg: "#FEF3C7",
      spark: [5.0, 4.8, 4.2, 3.9, 3.5, 3.0, 2.5],
      trend: "-0.5%",
      up: false,
    },
  ];

  const usageTrendData = dashboardStats?.usage_trend || USAGE_TREND_DATA;
  const monthlyData = dashboardStats?.monthly_data || MONTHLY_DATA;

  return (
    <div className="app-fullheight"
      style={{
        display: "flex",
        height: "100vh",
        overflow: "hidden",
        background: "var(--body-bg)",
      }}
    >
      {/* ── Sidebar ── */}
      <Sidebar agentStatuses={agentStatuses} />

      {/* ── Main Content ── */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", minWidth: 0 }}>

        {/* ── Topbar (TailAdmin style) ── */}
        <header className="ta-topbar">
          {/* Left: Search Bar with Live Instant Overlay */}
          <div style={{ display: "flex", alignItems: "center", gap: "12px", flex: 1, maxWidth: "460px", position: "relative" }}>
            <div style={{ position: "relative", flex: 1 }}>
              <Search
                size={16}
                style={{
                  position: "absolute",
                  left: "12px",
                  top: "50%",
                  transform: "translateY(-50%)",
                  color: "var(--text-light)",
                }}
              />
              <input
                type="text"
                value={navSearch}
                onChange={(e) => setNavSearch(e.target.value)}
                onFocus={() => setIsSearchFocused(true)}
                onBlur={() => setTimeout(() => setIsSearchFocused(false), 200)}
                placeholder="Tìm kiếm nhanh chatbot, nhân viên, kho tri thức..."
                className="ta-input"
                style={{ paddingLeft: "38px", paddingRight: navSearch ? "32px" : "12px", fontSize: "0.85rem", height: "40px" }}
              />

              {/* Quick Clear Button */}
              {navSearch && (
                <button
                  onClick={() => setNavSearch("")}
                  style={{
                    position: "absolute",
                    right: "10px",
                    top: "50%",
                    transform: "translateY(-50%)",
                    background: "none",
                    border: "none",
                    color: "var(--text-muted)",
                    cursor: "pointer",
                    fontSize: "0.8rem",
                    fontWeight: 700,
                  }}
                >
                  ✕
                </button>
              )}

              {/* Live Search Results Dropdown Overlay */}
              {isSearchFocused && navSearch.trim().length > 0 && (
                <div
                  style={{
                    position: "absolute",
                    top: "46px",
                    left: 0,
                    right: 0,
                    background: "#FFFFFF",
                    border: "1px solid var(--border)",
                    borderRadius: "12px",
                    boxShadow: "var(--shadow-lg)",
                    zIndex: 100,
                    maxHeight: "360px",
                    overflowY: "auto",
                    padding: "8px",
                  }}
                >
                  {/* Category 1: AI Chatbots */}
                  <div style={{ fontSize: "0.72rem", fontWeight: 700, color: "var(--text-muted)", padding: "6px 10px", textTransform: "uppercase" }}>
                    🤖 AI Agents ({chatbotsList.length})
                  </div>
                  {chatbotsList.length === 0 ? (
                    <div style={{ padding: "8px 10px", fontSize: "0.8rem", color: "var(--text-muted)" }}>Không tìm thấy AI Agent phù hợp</div>
                  ) : (
                    chatbotsList.slice(0, 4).map((bot) => (
                      <div
                        key={bot.id}
                        onClick={() => router.push(`/agents/${bot.role_code || 'CEO'}`)}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "space-between",
                          padding: "8px 10px",
                          borderRadius: "8px",
                          cursor: "pointer",
                          transition: "background 0.15s ease",
                        }}
                        onMouseDown={(e) => e.preventDefault()}
                        onMouseEnter={(e) => (e.currentTarget.style.background = "#F8FAFC")}
                        onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                      >
                        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                          <span style={{ fontSize: "1.1rem" }}>{bot.emoji || "🤖"}</span>
                          <span style={{ fontWeight: 600, fontSize: "0.85rem", color: "var(--text-dark)" }}>{bot.name}</span>
                        </div>
                        <span style={{ fontSize: "0.75rem", color: "var(--primary)", fontWeight: 500 }}>
                          {bot.dept} ›
                        </span>
                      </div>
                    ))
                  )}

                  {/* Category 2: Employees */}
                  <div style={{ fontSize: "0.72rem", fontWeight: 700, color: "var(--text-muted)", padding: "10px 10px 6px", borderTop: "1px solid var(--border)", marginTop: "6px", textTransform: "uppercase" }}>
                    👤 Nhân Viên ({topEmployeesList.length})
                  </div>
                  {topEmployeesList.length === 0 ? (
                    <div style={{ padding: "8px 10px", fontSize: "0.8rem", color: "var(--text-muted)" }}>Không tìm thấy nhân viên phù hợp</div>
                  ) : (
                    topEmployeesList.slice(0, 3).map((emp) => (
                      <div
                        key={emp.id}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "space-between",
                          padding: "8px 10px",
                          borderRadius: "8px",
                        }}
                      >
                        <span style={{ fontWeight: 500, fontSize: "0.85rem", color: "var(--text-dark)" }}>{emp.name}</span>
                        <span style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>{emp.dept}</span>
                      </div>
                    ))
                  )}

                  {/* Category 3: Knowledge RAG Quick Link */}
                  <div
                    onClick={() => router.push("/knowledge")}
                    onMouseDown={(e) => e.preventDefault()}
                    style={{
                      marginTop: "6px",
                      padding: "8px 10px",
                      borderTop: "1px solid var(--border)",
                      fontSize: "0.82rem",
                      fontWeight: 600,
                      color: "var(--primary)",
                      cursor: "pointer",
                      display: "flex",
                      alignItems: "center",
                      gap: "6px",
                    }}
                  >
                    📚 Tra cứu Kho Tri Thức RAG cho &quot;{navSearch}&quot; ›
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Right: Actions */}
          <div className="wrap-mobile" style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            {/* Date */}
            <div className="hide-mobile"
              style={{
                display: "flex",
                alignItems: "center",
                gap: "6px",
                padding: "8px 12px",
                background: "var(--body-bg)",
                border: "1px solid var(--border)",
                borderRadius: "8px",
                fontSize: "0.82rem",
                color: "var(--text-muted)",
                cursor: "pointer",
              }}
            >
              <CalendarDays size={14} />
              <span>
                {new Date().toLocaleDateString("vi-VN", {
                  day: "2-digit",
                  month: "short",
                  year: "numeric",
                })}
              </span>
            </div>

            {/* Export Excel */}
            <button
              id="export-excel-btn"
              onClick={handleExportExcel}
              disabled={exportingExcel}
              className="ta-btn ta-btn-ghost"
              style={{
                padding: "8px 14px",
                fontSize: "0.82rem",
                color: "#059669",
                borderColor: "#A7F3D0",
                background: "#F0FDF4",
              }}
            >
              <FileSpreadsheet size={14} />
              {exportingExcel ? "Đang xuất..." : "Excel"}
            </button>

            {/* Export PDF */}
            <button
              id="export-pdf-btn"
              onClick={handleExportPdf}
              disabled={exportingPdf}
              className="ta-btn ta-btn-ghost"
              style={{
                padding: "8px 14px",
                fontSize: "0.82rem",
                color: "#DC2626",
                borderColor: "#FECACA",
                background: "#FFF5F5",
              }}
            >
              <FileText size={14} />
              {exportingPdf ? "Đang xuất..." : "PDF"}
            </button>

            {/* Notification Bell */}
            <button
              style={{
                position: "relative",
                padding: "8px",
                borderRadius: "8px",
                border: "1px solid var(--border)",
                background: "var(--card-bg)",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color: "var(--text-muted)",
                transition: "all 0.15s ease",
              }}
            >
              <Bell size={17} />
              <span
                style={{
                  position: "absolute",
                  top: "6px",
                  right: "6px",
                  width: "8px",
                  height: "8px",
                  borderRadius: "50%",
                  background: "#EF4444",
                  border: "2px solid white",
                }}
              />
            </button>

            {/* Refresh */}
            <button
              onClick={fetchAgents}
              style={{
                padding: "8px",
                borderRadius: "8px",
                border: "1px solid var(--border)",
                background: "var(--card-bg)",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                color: "var(--text-muted)",
                transition: "all 0.15s ease",
              }}
            >
              <RefreshCw size={16} className={loading ? "animate-spin" : ""} />
            </button>

            {/* Divider */}
            <div
              style={{
                width: "1px",
                height: "32px",
                background: "var(--border)",
                margin: "0 4px",
              }}
            />

            {/* User Avatar */}
            <div
              style={{
                width: "38px",
                height: "38px",
                borderRadius: "50%",
                background: "linear-gradient(135deg, #6366f1, #8b5cf6)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color: "white",
                fontSize: "0.85rem",
                fontWeight: 700,
                cursor: "pointer",
                flexShrink: 0,
              }}
            >
              {user?.full_name?.charAt(0)?.toUpperCase() || "A"}
            </div>
          </div>
        </header>

        {/* ── Page Body ── */}
        <main className="page-main"
          style={{
            flex: 1,
            overflowY: "auto",
            padding: "24px 28px",
          }}
        >
          {/* ── Page Header ── */}
          <div style={{ marginBottom: "24px" }}>
            {/* Breadcrumb */}
            <div className="breadcrumb" style={{ marginBottom: "8px" }}>
              <span>Home</span>
              <span className="breadcrumb-sep">›</span>
              <span className="breadcrumb-current">CEO Dashboard</span>
            </div>

            {/* Title row */}
            <div className="wrap-mobile" style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <div>
                <h1
                  style={{
                    fontSize: "1.5rem",
                    fontWeight: 700,
                    color: "var(--text-dark)",
                    lineHeight: 1.3,
                  }}
                >
                  {greeting},{" "}
                  <span
                    style={{
                      background: "linear-gradient(135deg, #3C50E0, #8b5cf6)",
                      WebkitBackgroundClip: "text",
                      WebkitTextFillColor: "transparent",
                      backgroundClip: "text",
                    }}
                  >
                    {user?.full_name?.split(" ").pop() || "CEO"}
                  </span>{" "}
                  👋
                </h1>
                <p style={{ color: "var(--text-muted)", fontSize: "0.875rem", marginTop: "4px" }}>
                  Tổng quan hoạt động Chatbot & Nhân viên của tổ chức {activeTab === "day" ? "hôm nay" : activeTab === "week" ? "trong 7 ngày gần nhất" : "trong tháng này"}.
                </p>
              </div>

              {/* Tab period selector */}
              <div
                style={{
                  display: "flex",
                  gap: "2px",
                  background: "#E2E8F0",
                  borderRadius: "10px",
                  padding: "4px",
                }}
              >
                {(["day", "week", "month"] as const).map((tab) => (
                  <button
                    key={tab}
                    onClick={() => setActiveTab(tab)}
                    style={{
                      padding: "6px 16px",
                      borderRadius: "7px",
                      fontSize: "0.82rem",
                      fontWeight: 600,
                      border: "none",
                      cursor: "pointer",
                      transition: "all 0.2s ease",
                      background: activeTab === tab ? "#FFFFFF" : "transparent",
                      color: activeTab === tab ? "var(--text-dark)" : "var(--text-muted)",
                      boxShadow: activeTab === tab ? "var(--shadow-sm)" : "none",
                    }}
                  >
                    {tab === "day" ? "Ngày" : tab === "week" ? "Tuần" : "Tháng"}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* ── KPI Cards ── */}
          <div className="kpi-row"
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(4, 1fr)",
              gap: "20px",
              marginBottom: "24px",
            }}
          >
            {kpiCards.map((kpi) => (
              <div key={kpi.id} id={kpi.id} className="kpi-card fade-in-up">
                {/* Top row: icon + trend */}
                <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
                  <div
                    className="icon-box"
                    style={{ background: kpi.iconBg, color: kpi.color }}
                  >
                    <kpi.icon size={21} />
                  </div>

                  <span
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: "3px",
                      padding: "4px 10px",
                      borderRadius: "999px",
                      fontSize: "0.75rem",
                      fontWeight: 700,
                      color: kpi.up ? "#065F46" : "#92400E",
                      background: kpi.up ? "#D1FAE5" : "#FEF3C7",
                    }}
                  >
                    {kpi.up ? <ArrowUpRight size={13} /> : <ArrowDownRight size={13} />}
                    {kpi.trend}
                  </span>
                </div>

                {/* Bottom row: value + sparkline */}
                <div
                  style={{
                    display: "flex",
                    alignItems: "flex-end",
                    justifyContent: "space-between",
                    marginTop: "12px",
                  }}
                >
                  <div>
                    <div
                      style={{
                        fontSize: "1.75rem",
                        fontWeight: 800,
                        color: "var(--text-dark)",
                        lineHeight: 1,
                        letterSpacing: "-0.02em",
                      }}
                    >
                      {kpi.value}
                    </div>
                    <div
                      style={{
                        fontSize: "0.82rem",
                        color: "var(--text-muted)",
                        marginTop: "5px",
                        fontWeight: 500,
                      }}
                    >
                      {kpi.label}
                    </div>
                    <div style={{ fontSize: "0.75rem", color: "var(--text-light)", marginTop: "2px" }}>
                      {kpi.sub}
                    </div>
                  </div>
                  <Sparkline data={kpi.spark} color={kpi.color} />
                </div>
              </div>
            ))}
          </div>

          {/* ── Charts Row ── */}
          <div className="stack-mobile"
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 360px",
              gap: "20px",
              marginBottom: "24px",
            }}
          >
            {/* Area Chart */}
            <div className="ta-card" style={{ padding: "22px" }}>
              <div
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  justifyContent: "space-between",
                  marginBottom: "20px",
                }}
              >
                <div>
                  <h2 style={{ fontSize: "1rem", fontWeight: 700, color: "var(--text-dark)" }}>
                    Xu Hướng Sử Dụng & Tương Tác
                  </h2>
                  <p style={{ fontSize: "0.78rem", color: "var(--text-light)", marginTop: "3px" }}>
                    AI tự động xử lý vs Handoff sang nhân viên
                  </p>
                </div>
                <button
                  style={{
                    padding: "6px",
                    borderRadius: "6px",
                    border: "1px solid var(--border)",
                    background: "transparent",
                    cursor: "pointer",
                    color: "var(--text-light)",
                  }}
                >
                  <MoreHorizontal size={16} />
                </button>
              </div>

              <ResponsiveContainer width="100%" height={220}>
                <AreaChart data={usageTrendData} margin={{ top: 5, right: 5, left: -20, bottom: 0 }}>
                  <defs>
                    <linearGradient id="aiGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#3C50E0" stopOpacity={0.15} />
                      <stop offset="95%" stopColor="#3C50E0" stopOpacity={0} />
                    </linearGradient>
                    <linearGradient id="handoffGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#F59E0B" stopOpacity={0.15} />
                      <stop offset="95%" stopColor="#F59E0B" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" vertical={false} />
                  <XAxis
                    dataKey="day"
                    tick={{ fontSize: 12, fill: "#94A3B8" }}
                    axisLine={false}
                    tickLine={false}
                  />
                  <YAxis
                    tick={{ fontSize: 12, fill: "#94A3B8" }}
                    axisLine={false}
                    tickLine={false}
                  />
                  <Tooltip content={<CustomTooltip />} />
                  <Legend
                    iconType="circle"
                    iconSize={8}
                    formatter={(value) =>
                      value === "ai" ? "AI Tự động" : "Handoff"
                    }
                    wrapperStyle={{ fontSize: "12px", paddingTop: "12px", color: "#64748B" }}
                  />
                  <Area
                    type="monotone"
                    dataKey="ai"
                    stroke="#3C50E0"
                    strokeWidth={2.5}
                    fill="url(#aiGrad)"
                    dot={false}
                    activeDot={{ r: 5, fill: "#3C50E0", strokeWidth: 0 }}
                  />
                  <Area
                    type="monotone"
                    dataKey="handoff"
                    stroke="#F59E0B"
                    strokeWidth={2.5}
                    fill="url(#handoffGrad)"
                    dot={false}
                    activeDot={{ r: 5, fill: "#F59E0B", strokeWidth: 0 }}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>

            {/* Monthly Bar Chart + Target */}
            <div className="ta-card" style={{ padding: "22px" }}>
              <div style={{ marginBottom: "16px" }}>
                <h2 style={{ fontSize: "0.95rem", fontWeight: 700, color: "var(--text-dark)" }}>
                  Monthly Messages
                </h2>
                <p style={{ fontSize: "0.75rem", color: "var(--text-light)", marginTop: "3px" }}>
                  Số lượng hội thoại từng tháng
                </p>
              </div>

              <ResponsiveContainer width="100%" height={160}>
                <BarChart data={monthlyData} margin={{ top: 0, right: 0, left: -20, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" vertical={false} />
                  <XAxis
                    dataKey="month"
                    tick={{ fontSize: 10, fill: "#94A3B8" }}
                    axisLine={false}
                    tickLine={false}
                  />
                  <YAxis tick={{ fontSize: 10, fill: "#94A3B8" }} axisLine={false} tickLine={false} />
                  <Tooltip
                    cursor={{ fill: "rgba(60,80,224,0.05)" }}
                    contentStyle={{
                      background: "#fff",
                      border: "1px solid var(--border)",
                      borderRadius: "8px",
                      fontSize: "12px",
                    }}
                  />
                  <Bar dataKey="value" fill="#3C50E0" radius={[4, 4, 0, 0]} maxBarSize={22} />
                </BarChart>
              </ResponsiveContainer>

              {/* Mini stats */}
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr 1fr",
                  gap: "8px",
                  marginTop: "16px",
                  paddingTop: "16px",
                  borderTop: "1px solid var(--border)",
                }}
              >
                {[
                  { label: "Target", value: dashboardStats?.monthly_summary?.target || 500, up: false },
                  { label: "Completed", value: (dashboardStats?.monthly_summary?.completed ?? 0).toString(), up: true },
                  { label: "Avg/Day", value: (dashboardStats?.monthly_summary?.avg_per_day ?? 0).toString(), up: true },
                ].map((s) => (
                  <div key={s.label} style={{ textAlign: "center" }}>
                    <div
                      style={{
                        fontSize: "0.95rem",
                        fontWeight: 700,
                        color: "var(--text-dark)",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        gap: "2px",
                      }}
                    >
                      {s.value}
                      {s.up ? (
                        <ArrowUpRight size={13} color="#10B981" />
                      ) : (
                        <ArrowDownRight size={13} color="#EF4444" />
                      )}
                    </div>
                    <div style={{ fontSize: "0.7rem", color: "var(--text-light)", marginTop: "2px" }}>
                      {s.label}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* ── Bottom Grid: Table + Employee List ── */}
          <div className="stack-mobile"
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 0.52fr",
              gap: "20px",
            }}
          >
            {/* Chatbot Table */}
            <div className="ta-card" style={{ overflow: "hidden" }}>
              {/* Table Header */}
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "18px 20px",
                  borderBottom: "1px solid var(--border)",
                }}
              >
                <div>
                  <h3 style={{ fontSize: "0.95rem", fontWeight: 700, color: "var(--text-dark)" }}>
                    Active Chatbots Overview
                  </h3>
                  <p style={{ fontSize: "0.75rem", color: "var(--text-light)", marginTop: "2px" }}>
                    Danh sách chatbot đang hoạt động trong hệ thống
                  </p>
                </div>
                <button
                  style={{
                    padding: "6px",
                    borderRadius: "6px",
                    border: "1px solid var(--border)",
                    background: "transparent",
                    cursor: "pointer",
                    color: "var(--text-light)",
                  }}
                >
                  <MoreHorizontal size={15} />
                </button>
              </div>

              {/* Table */}
              <div style={{ overflowX: "auto" }}>
                <table className="ta-table">
                  <thead>
                    <tr>
                      <th>Tên Chatbot</th>
                      <th>Bộ phận</th>
                      <th style={{ textAlign: "right" }}>Hội thoại</th>
                      <th style={{ textAlign: "right" }}>Accuracy</th>
                      <th style={{ textAlign: "center" }}>Trạng thái</th>
                    </tr>
                  </thead>
                  <tbody>
                    {chatbotsList.map((bot) => (
                      <tr key={bot.id}>
                        <td>
                          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                            <div
                              style={{
                                width: "38px",
                                height: "38px",
                                borderRadius: "10px",
                                background: "var(--primary-light)",
                                display: "flex",
                                alignItems: "center",
                                justifyContent: "center",
                                fontSize: "1.1rem",
                                flexShrink: 0,
                              }}
                            >
                              {bot.emoji}
                            </div>
                            <span style={{ fontWeight: 600, color: "var(--text-dark)" }}>
                              {bot.name}
                            </span>
                          </div>
                        </td>
                        <td style={{ color: "var(--text-muted)" }}>{bot.dept}</td>
                        <td style={{ textAlign: "right", fontWeight: 600, color: "var(--text-dark)" }}>
                          {bot.conversations.toLocaleString()}
                        </td>
                        <td style={{ textAlign: "right" }}>
                          <span
                            style={{
                              fontWeight: 700,
                              color:
                                bot.accuracy >= 90
                                  ? "#059669"
                                  : bot.accuracy >= 80
                                  ? "#D97706"
                                  : "#DC2626",
                            }}
                          >
                            {bot.accuracy}%
                          </span>
                        </td>
                        <td style={{ textAlign: "center" }}>
                          <StatusBadge status={bot.status} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Table Footer */}
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "12px 20px",
                  borderTop: "1px solid var(--border)",
                  background: "#FAFBFC",
                }}
              >
                <span style={{ fontSize: "0.78rem", color: "var(--text-light)" }}>
                  {chatbotsList.length} chatbots · {chatbotsList.filter((b) => b.status === "active").length} đang hoạt động
                </span>
                <button
                  style={{
                    fontSize: "0.78rem",
                    color: "var(--primary)",
                    fontWeight: 600,
                    background: "none",
                    border: "none",
                    cursor: "pointer",
                    display: "flex",
                    alignItems: "center",
                    gap: "3px",
                  }}
                >
                  Xem tất cả <ChevronRight size={13} />
                </button>
              </div>
            </div>

            {/* Top Employee Users */}
            <div className="ta-card" style={{ overflow: "hidden" }}>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "18px 20px",
                  borderBottom: "1px solid var(--border)",
                }}
              >
                <div>
                  <h3 style={{ fontSize: "0.95rem", fontWeight: 700, color: "var(--text-dark)" }}>
                    Top Employee Users
                  </h3>
                  <p style={{ fontSize: "0.75rem", color: "var(--text-light)", marginTop: "2px" }}>
                    Nhân viên sử dụng chatbot nhiều nhất
                  </p>
                </div>
                <button
                  style={{
                    padding: "6px",
                    borderRadius: "6px",
                    border: "1px solid var(--border)",
                    background: "transparent",
                    cursor: "pointer",
                    color: "var(--text-light)",
                  }}
                >
                  <MoreHorizontal size={15} />
                </button>
              </div>

              <div style={{ padding: "18px 20px", display: "flex", flexDirection: "column", gap: "18px" }}>
                {topEmployeesList.map((emp, i: number) => (
                  <div key={emp.id} style={{ display: "flex", alignItems: "center", gap: "12px" }}>
                    {/* Rank */}
                    <span
                      style={{
                        fontSize: "0.72rem",
                        fontWeight: 700,
                        color: "var(--text-light)",
                        width: "16px",
                        textAlign: "center",
                        flexShrink: 0,
                      }}
                    >
                      {i + 1}
                    </span>

                    {/* Avatar */}
                    <div
                      style={{
                        width: "36px",
                        height: "36px",
                        borderRadius: "50%",
                        background: AVATAR_COLORS[i % AVATAR_COLORS.length],
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        color: "white",
                        fontSize: "0.75rem",
                        fontWeight: 700,
                        flexShrink: 0,
                      }}
                    >
                      {emp.avatar}
                    </div>

                    {/* Info + progress */}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div
                        style={{
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "space-between",
                          marginBottom: "5px",
                        }}
                      >
                        <span
                          style={{
                            fontSize: "0.85rem",
                            fontWeight: 600,
                            color: "var(--text-dark)",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          }}
                        >
                          {emp.name}
                        </span>
                        <span
                          style={{
                            fontSize: "0.75rem",
                            color: "var(--text-muted)",
                            flexShrink: 0,
                            marginLeft: "8px",
                          }}
                        >
                          {emp.msgs.toLocaleString()}
                        </span>
                      </div>

                      {/* Progress bar */}
                      <div
                        style={{
                          height: "5px",
                          background: "#E2E8F0",
                          borderRadius: "99px",
                          overflow: "hidden",
                          marginBottom: "4px",
                        }}
                      >
                        <div
                          style={{
                            width: `${emp.pct}%`,
                            height: "100%",
                            background: "linear-gradient(90deg, #3C50E0, #8b5cf6)",
                            borderRadius: "99px",
                            transition: "width 0.8s ease",
                          }}
                        />
                      </div>

                      <span style={{ fontSize: "0.7rem", color: "var(--text-light)" }}>
                        {emp.dept}
                      </span>
                    </div>
                  </div>
                ))}
              </div>

              <div
                style={{
                  padding: "12px 20px",
                  borderTop: "1px solid var(--border)",
                  background: "#FAFBFC",
                }}
              >
                <button
                  style={{
                    fontSize: "0.78rem",
                    color: "var(--primary)",
                    fontWeight: 600,
                    background: "none",
                    border: "none",
                    cursor: "pointer",
                    display: "flex",
                    alignItems: "center",
                    gap: "3px",
                  }}
                >
                  Xem tất cả nhân viên <ChevronRight size={13} />
                </button>
              </div>
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
