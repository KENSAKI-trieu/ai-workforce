"use client";

import { useEffect, useState } from "react";
import Sidebar from "@/components/Sidebar";
import api from "@/lib/api";
import {
  DollarSign,
  AlertTriangle,
  Cpu,
  Users,
  Bot,
  Layers,
  Building2,
  ShieldAlert,
  RefreshCw,
  Sliders,
  Zap,
  BarChart3,
  Plus,
} from "lucide-react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  Legend,
} from "recharts";

// Colors for Charts & Avatars
const MODEL_COLORS: Record<string, string> = {
  "gpt-4o": "#3C50E0",
  "gpt-4o-mini": "#0EA5E9",
  "gpt-3.5-turbo": "#10B981",
  "gemini-2.5-flash": "#8B5CF6",
  "gemini-3.6-flash": "#7C3AED",
  "gemini-3.5-flash-lite": "#A78BFA",
  "claude-sonnet-4": "#EC4899",
  "gemini-1.5-pro": "#F59E0B",
  "gemini-1.5-flash": "#8B5CF6",
  "claude-3-5-sonnet": "#EC4899",
  default: "#64748B",
};

const AGENT_EMOJIS: Record<string, string> = {
  CEO: "👔",
  HR: "🧑‍💼",
  LEGAL: "⚖️",
  IT: "💻",
  FINANCE: "💰",
  SALES: "📈",
  KNOWLEDGE: "📚",
};

type BudgetScope = "TENANT" | "DEPARTMENT" | "AGENT" | "USER";
type RoutingStrategy = "LOW_COST" | "BALANCED" | "HIGH_PERFORMANCE";

interface CostSummary {
  period_start: string;
  period_end: string;
  total_requests: number;
  total_prompt_tokens: number;
  total_cached_prompt_tokens: number;
  total_completion_tokens: number;
  total_tokens: number;
  total_estimated_cost_usd: number;
  monthly_budget_usd: number;
  budget_usage_pct: number;
  estimated_savings_usd: number;
  savings_baseline_model: string;
  active_models_count: number;
  legacy_records_excluded: number;
}

interface UsageTotals {
  requests: number;
  prompt_tokens: number;
  cached_prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  total_cost_usd: number;
}

interface AgentCost extends UsageTotals {
  agent_role: string;
  models_used: string[];
}

interface EmployeeCost extends UsageTotals {
  user_id: string;
  full_name: string;
  email: string | null;
  department: string;
}

interface DepartmentCost extends UsageTotals {
  department: string;
}

interface WorkflowCost extends UsageTotals {
  workflow_id: string;
  title: string;
  status: string;
  last_active: string | null;
}

interface DailyTrend {
  date: string;
  prompt_tokens: number;
  cached_prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_usd: number;
}

interface ModelDistribution {
  model_name: string;
  requests: number;
  prompt_tokens: number;
  cached_prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
}

interface TokenStatistics {
  daily_trends: DailyTrend[];
  model_distribution: ModelDistribution[];
}

interface CostAlert {
  id: string;
  severity: "MEDIUM" | "HIGH";
  title: string;
  message: string;
  timestamp: string;
}

interface BudgetUsage {
  id: string;
  scope_type: BudgetScope;
  scope_id: string;
  monthly_budget_usd: number;
  alert_threshold_pct: number;
  current_spend_usd: number;
  usage_pct: number;
  status: "NORMAL" | "WARNING" | "EXCEEDED" | "INACTIVE";
  is_active: boolean;
}

interface BudgetsAlerts {
  period_start: string;
  period_end: string;
  budgets: BudgetUsage[];
  alerts: CostAlert[];
  total_alerts_count: number;
}

interface RoutingRule {
  id: string;
  task_type: string;
  agent_role: string;
  preferred_model: string;
  fallback_model: string;
  max_tokens: number;
  cost_saving_strategy: RoutingStrategy;
  is_active: boolean;
}

interface BudgetForm {
  scope_type: BudgetScope;
  scope_id: string;
  monthly_budget_usd: number;
  alert_threshold_pct: number;
  is_active: boolean;
}

interface RoutingRuleForm {
  id?: string;
  task_type: string;
  agent_role: string;
  preferred_model: string;
  fallback_model: string;
  max_tokens: number;
  cost_saving_strategy: RoutingStrategy;
  is_active: boolean;
}

const EMPTY_SUMMARY: CostSummary = {
  period_start: "",
  period_end: "",
  total_requests: 0,
  total_prompt_tokens: 0,
  total_cached_prompt_tokens: 0,
  total_completion_tokens: 0,
  total_tokens: 0,
  total_estimated_cost_usd: 0,
  monthly_budget_usd: 0,
  budget_usage_pct: 0,
  estimated_savings_usd: 0,
  savings_baseline_model: "gpt-4o",
  active_models_count: 0,
  legacy_records_excluded: 0,
};

export default function CostsManagementPage() {
  const [activeTab, setActiveTab] = useState<"analytics" | "breakdowns" | "budgets" | "routing">("analytics");
  const [breakdownType, setBreakdownType] = useState<"agent" | "employee" | "department" | "workflow">("agent");

  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // API Data States
  const [summary, setSummary] = useState<CostSummary>(EMPTY_SUMMARY);
  const [tokenStats, setTokenStats] = useState<TokenStatistics>({ daily_trends: [], model_distribution: [] });
  const [agentCosts, setAgentCosts] = useState<AgentCost[]>([]);
  const [employeeCosts, setEmployeeCosts] = useState<EmployeeCost[]>([]);
  const [deptCosts, setDeptCosts] = useState<DepartmentCost[]>([]);
  const [workflowCosts, setWorkflowCosts] = useState<WorkflowCost[]>([]);
  const [budgetsAlerts, setBudgetsAlerts] = useState<BudgetsAlerts>({
    period_start: "",
    period_end: "",
    budgets: [],
    alerts: [],
    total_alerts_count: 0,
  });
  const [routingRules, setRoutingRules] = useState<RoutingRule[]>([]);

  // Modal / Form state for Budgets
  const [editingBudget, setEditingBudget] = useState<BudgetUsage | "new" | null>(null);
  const [budgetForm, setBudgetForm] = useState<BudgetForm>({
    scope_type: "DEPARTMENT",
    scope_id: "HR",
    monthly_budget_usd: 100,
    alert_threshold_pct: 80,
    is_active: true,
  });

  // Modal / Form state for Routing Rules
  const [editingRule, setEditingRule] = useState<RoutingRule | "new" | null>(null);
  const [ruleForm, setRuleForm] = useState<RoutingRuleForm>({
    task_type: "",
    agent_role: "HR",
    preferred_model: "gpt-3.5-turbo",
    fallback_model: "gpt-4o",
    max_tokens: 2048,
    cost_saving_strategy: "LOW_COST",
    is_active: true,
  });

  const fetchAllData = async () => {
    try {
      setRefreshing(true);
      setErrorMessage(null);
      const [sumRes, statsRes, agentRes, empRes, deptRes, wfRes, budgetRes, routingRes] = await Promise.all([
        api.get<CostSummary>("/api/v1/costs/summary"),
        api.get<TokenStatistics>("/api/v1/costs/token-stats"),
        api.get<AgentCost[]>("/api/v1/costs/by-agent"),
        api.get<EmployeeCost[]>("/api/v1/costs/by-employee"),
        api.get<DepartmentCost[]>("/api/v1/costs/by-department"),
        api.get<WorkflowCost[]>("/api/v1/costs/by-workflow"),
        api.get<BudgetsAlerts>("/api/v1/costs/budgets-alerts"),
        api.get<RoutingRule[]>("/api/v1/costs/model-routing"),
      ]);

      setSummary(sumRes.data);
      setTokenStats(statsRes.data);
      setAgentCosts(agentRes.data);
      setEmployeeCosts(empRes.data);
      setDeptCosts(deptRes.data);
      setWorkflowCosts(wfRes.data);
      setBudgetsAlerts(budgetRes.data);
      setRoutingRules(routingRes.data);
    } catch {
      setErrorMessage("Không thể tải đầy đủ dữ liệu chi phí. Vui lòng kiểm tra kết nối backend và thử lại.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void fetchAllData();
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);

  // Save Budget Limit
  const handleSaveBudget = async () => {
    try {
      await api.post("/api/v1/costs/budgets", budgetForm);
      setEditingBudget(null);
      fetchAllData();
    } catch {
      alert("Lỗi khi lưu cấu hình ngân sách");
    }
  };

  // Save Model Routing Rule
  const handleSaveRoutingRule = async () => {
    try {
      await api.post("/api/v1/costs/model-routing", ruleForm);
      setEditingRule(null);
      fetchAllData();
    } catch {
      alert("Lỗi khi lưu quy tắc điều hướng model");
    }
  };

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "#F8FAFC" }}>
      {/* Navigation Sidebar */}
      <Sidebar />

      {/* Main Content Area */}
      <div style={{ flex: 1, padding: "28px 36px", overflowX: "hidden" }}>
        {/* Header Title Section */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "24px" }}>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
              <div
                style={{
                  width: "40px",
                  height: "40px",
                  borderRadius: "10px",
                  background: "linear-gradient(135deg, #10B981, #059669)",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  color: "#FFF",
                  boxShadow: "0 4px 12px rgba(16,185,129,0.3)",
                }}
              >
                <DollarSign size={22} />
              </div>
              <div>
                <h1 style={{ fontSize: "1.5rem", fontWeight: 800, color: "#0F172A", margin: 0 }}>
                  Quản Lý Chi Phí & Token AI
                </h1>
                <p style={{ fontSize: "0.875rem", color: "#64748B", margin: "2px 0 0 0" }}>
                  Theo dõi chi tiết Token tiêu thụ, kiểm soát Ngân sách tháng, Cảnh báo vượt ngưỡng & Tối ưu chọn Model.
                </p>
              </div>
            </div>
          </div>

          <button
            onClick={fetchAllData}
            disabled={refreshing}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "8px",
              padding: "9px 16px",
              borderRadius: "8px",
              background: "#FFFFFF",
              border: "1px solid #E2E8F0",
              fontSize: "0.875rem",
              fontWeight: 600,
              color: "#334155",
              cursor: "pointer",
              boxShadow: "0 1px 2px rgba(0,0,0,0.05)",
            }}
          >
            <RefreshCw size={16} className={refreshing ? "animate-spin" : ""} />
            Làm mới dữ liệu
          </button>
        </div>

        {loading && (
          <div
            role="status"
            style={{
              marginBottom: "20px",
              padding: "12px 16px",
              borderRadius: "10px",
              background: "#EEF2FF",
              color: "#3730A3",
              fontSize: "0.875rem",
            }}
          >
            Đang tải usage và chi phí đã được đo từ nhà cung cấp…
          </div>
        )}

        {errorMessage && (
          <div
            role="alert"
            style={{
              marginBottom: "20px",
              padding: "12px 16px",
              borderRadius: "10px",
              background: "#FEF2F2",
              border: "1px solid #FECACA",
              color: "#B91C1C",
              fontSize: "0.875rem",
            }}
          >
            {errorMessage}
          </div>
        )}

        {summary.legacy_records_excluded > 0 && (
          <div
            style={{
              marginBottom: "20px",
              padding: "12px 16px",
              borderRadius: "10px",
              background: "#FFFBEB",
              border: "1px solid #FDE68A",
              color: "#92400E",
              fontSize: "0.82rem",
            }}
          >
            Đã loại {summary.legacy_records_excluded.toLocaleString()} bản ghi ước lượng cũ khỏi báo cáo tháng này.
          </div>
        )}

        {!loading && !errorMessage && summary.total_requests === 0 && (
          <div
            style={{
              marginBottom: "20px",
              padding: "14px 16px",
              borderRadius: "10px",
              background: "#F8FAFC",
              border: "1px solid #CBD5E1",
              color: "#475569",
              fontSize: "0.85rem",
            }}
          >
            Chưa có usage do nhà cung cấp xác nhận trong kỳ này. Các tác vụ nội bộ không gọi LLM sẽ không phát sinh chi phí.
          </div>
        )}

        {/* Global Alert Notification Banner (if any high alerts exist) */}
        {budgetsAlerts.alerts && budgetsAlerts.alerts.length > 0 && (
          <div
            style={{
              marginBottom: "24px",
              padding: "14px 20px",
              borderRadius: "12px",
              background: budgetsAlerts.alerts.some((alert) => alert.severity === "HIGH") ? "#FEF2F2" : "#FFFBEB",
              border: `1px solid ${budgetsAlerts.alerts.some((alert) => alert.severity === "HIGH") ? "#FECACA" : "#FDE68A"}`,
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
              <ShieldAlert
                size={22}
                color={budgetsAlerts.alerts.some((alert) => alert.severity === "HIGH") ? "#DC2626" : "#D97706"}
              />
              <div>
                <div
                  style={{
                    fontSize: "0.9rem",
                    fontWeight: 700,
                    color: budgetsAlerts.alerts.some((alert) => alert.severity === "HIGH") ? "#991B1B" : "#92400E",
                  }}
                >
                  Cảnh báo hệ thống chi phí ({budgetsAlerts.alerts.length} thông báo)
                </div>
                <div
                  style={{
                    fontSize: "0.825rem",
                    color: budgetsAlerts.alerts.some((alert) => alert.severity === "HIGH") ? "#B91C1C" : "#B45309",
                  }}
                >
                  {budgetsAlerts.alerts[0]?.message}
                </div>
              </div>
            </div>
            <button
              onClick={() => setActiveTab("budgets")}
              style={{
                fontSize: "0.8rem",
                fontWeight: 700,
                color: budgetsAlerts.alerts.some((alert) => alert.severity === "HIGH") ? "#DC2626" : "#D97706",
                background: "none",
                border: "none",
                cursor: "pointer",
                textDecoration: "underline",
              }}
            >
              Xem chi tiết
            </button>
          </div>
        )}

        {/* ── Metric Summary Cards (Grid 4) ── */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
            gap: "18px",
            marginBottom: "28px",
          }}
        >
          {/* Card 1: Total Spend vs Budget */}
          <div
            style={{
              background: "#FFFFFF",
              borderRadius: "14px",
              padding: "20px",
              border: "1px solid #E2E8F0",
              boxShadow: "0 2px 4px rgba(0,0,0,0.02)",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "10px" }}>
              <span style={{ fontSize: "0.8rem", fontWeight: 600, color: "#64748B" }}>Tổng Chi Phí Tháng</span>
              <span
                style={{
                  fontSize: "0.75rem",
                  fontWeight: 700,
                  padding: "3px 8px",
                  borderRadius: "20px",
                  background: summary.budget_usage_pct >= 100 ? "#FEE2E2" : summary.budget_usage_pct >= 80 ? "#FEF3C7" : "#D1FAE5",
                  color: summary.budget_usage_pct >= 100 ? "#DC2626" : summary.budget_usage_pct >= 80 ? "#D97706" : "#059669",
                }}
              >
                {summary.monthly_budget_usd > 0 ? `${summary.budget_usage_pct}% Budget` : "Chưa cấu hình"}
              </span>
            </div>
            <div style={{ fontSize: "1.6rem", fontWeight: 800, color: "#0F172A", marginBottom: "6px" }}>
              ${summary.total_estimated_cost_usd.toFixed(2)}
              <span style={{ fontSize: "0.85rem", fontWeight: 500, color: "#94A3B8", marginLeft: "6px" }}>
                {summary.monthly_budget_usd > 0 ? `/ $${summary.monthly_budget_usd}` : ""}
              </span>
            </div>
            {/* Progress bar */}
            <div style={{ width: "100%", height: "7px", background: "#F1F5F9", borderRadius: "10px", overflow: "hidden" }}>
              <div
                style={{
                  width: `${Math.min(summary.budget_usage_pct, 100)}%`,
                  height: "100%",
                  background: summary.budget_usage_pct >= 100 ? "#EF4444" : summary.budget_usage_pct >= 80 ? "#F59E0B" : "#10B981",
                  borderRadius: "10px",
                  transition: "width 0.5s ease",
                }}
              />
            </div>
          </div>

          {/* Card 2: Input / Output Token Stats */}
          <div
            style={{
              background: "#FFFFFF",
              borderRadius: "14px",
              padding: "20px",
              border: "1px solid #E2E8F0",
              boxShadow: "0 2px 4px rgba(0,0,0,0.02)",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "10px" }}>
              <span style={{ fontSize: "0.8rem", fontWeight: 600, color: "#64748B" }}>Tổng LLM Token Tiêu Thụ</span>
              <Cpu size={18} color="#3C50E0" />
            </div>
            <div style={{ fontSize: "1.6rem", fontWeight: 800, color: "#0F172A", marginBottom: "4px" }}>
              {(summary.total_tokens / 1000).toFixed(1)}k
              <span style={{ fontSize: "0.85rem", fontWeight: 500, color: "#64748B", marginLeft: "6px" }}>Tokens</span>
            </div>
            <div style={{ display: "flex", gap: "12px", fontSize: "0.78rem", color: "#64748B", marginTop: "6px" }}>
              <span>📥 Input: <b>{(summary.total_prompt_tokens / 1000).toFixed(1)}k</b></span>
              <span>📤 Output: <b>{(summary.total_completion_tokens / 1000).toFixed(1)}k</b></span>
            </div>
          </div>

          {/* Card 3: Threshold Alerts Count */}
          <div
            style={{
              background: "#FFFFFF",
              borderRadius: "14px",
              padding: "20px",
              border: "1px solid #E2E8F0",
              boxShadow: "0 2px 4px rgba(0,0,0,0.02)",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "10px" }}>
              <span style={{ fontSize: "0.8rem", fontWeight: 600, color: "#64748B" }}>Cảnh Báo Vượt Ngưỡng</span>
              <AlertTriangle size={18} color={budgetsAlerts.total_alerts_count > 0 ? "#F59E0B" : "#10B981"} />
            </div>
            <div style={{ fontSize: "1.6rem", fontWeight: 800, color: "#0F172A", marginBottom: "4px" }}>
              {budgetsAlerts.total_alerts_count}
              <span style={{ fontSize: "0.85rem", fontWeight: 500, color: "#64748B", marginLeft: "6px" }}>Cảnh báo</span>
            </div>
            <div style={{ fontSize: "0.78rem", color: budgetsAlerts.total_alerts_count > 0 ? "#D97706" : "#10B981" }}>
              {budgetsAlerts.total_alerts_count > 0 ? "Cần kiểm tra hạn mức ngân sách" : "Các hạn mức ở trạng thái an toàn"}
            </div>
          </div>

          {/* Card 4: Estimated Savings from Model Routing */}
          <div
            style={{
              background: "#FFFFFF",
              borderRadius: "14px",
              padding: "20px",
              border: "1px solid #E2E8F0",
              boxShadow: "0 2px 4px rgba(0,0,0,0.02)",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "10px" }}>
              <span style={{ fontSize: "0.8rem", fontWeight: 600, color: "#64748B" }}>Ước Tính Tiết Kiệm (Model Routing)</span>
              <Zap size={18} color="#10B981" />
            </div>
            <div style={{ fontSize: "1.6rem", fontWeight: 800, color: "#059669", marginBottom: "4px" }}>
              +${summary.estimated_savings_usd.toFixed(2)}
            </div>
            <div style={{ fontSize: "0.78rem", color: "#64748B" }}>
              Chênh lệch thực tế so với baseline {summary.savings_baseline_model}
            </div>
          </div>
        </div>

        {/* ── Main Navigation Tabs ── */}
        <div
          style={{
            display: "flex",
            gap: "8px",
            borderBottom: "1px solid #E2E8F0",
            marginBottom: "24px",
          }}
        >
          <button
            onClick={() => setActiveTab("analytics")}
            style={{
              padding: "12px 20px",
              fontSize: "0.9rem",
              fontWeight: activeTab === "analytics" ? 700 : 500,
              color: activeTab === "analytics" ? "#3C50E0" : "#64748B",
              borderBottom: activeTab === "analytics" ? "2px solid #3C50E0" : "2px solid transparent",
              background: "none",
              borderLeft: "none",
              borderRight: "none",
              borderTop: "none",
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              gap: "8px",
            }}
          >
            <BarChart3 size={17} />
            Thống Kê Token & Chi Phí
          </button>

          <button
            onClick={() => setActiveTab("breakdowns")}
            style={{
              padding: "12px 20px",
              fontSize: "0.9rem",
              fontWeight: activeTab === "breakdowns" ? 700 : 500,
              color: activeTab === "breakdowns" ? "#3C50E0" : "#64748B",
              borderBottom: activeTab === "breakdowns" ? "2px solid #3C50E0" : "2px solid transparent",
              background: "none",
              borderLeft: "none",
              borderRight: "none",
              borderTop: "none",
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              gap: "8px",
            }}
          >
            <Layers size={17} />
            Phân Rã Chi Phí (4 Chiều)
          </button>

          <button
            onClick={() => setActiveTab("budgets")}
            style={{
              padding: "12px 20px",
              fontSize: "0.9rem",
              fontWeight: activeTab === "budgets" ? 700 : 500,
              color: activeTab === "budgets" ? "#3C50E0" : "#64748B",
              borderBottom: activeTab === "budgets" ? "2px solid #3C50E0" : "2px solid transparent",
              background: "none",
              borderLeft: "none",
              borderRight: "none",
              borderTop: "none",
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              gap: "8px",
            }}
          >
            <ShieldAlert size={17} />
            Giới Hạn Ngân Sách & Cảnh Báo
          </button>

          <button
            onClick={() => setActiveTab("routing")}
            style={{
              padding: "12px 20px",
              fontSize: "0.9rem",
              fontWeight: activeTab === "routing" ? 700 : 500,
              color: activeTab === "routing" ? "#3C50E0" : "#64748B",
              borderBottom: activeTab === "routing" ? "2px solid #3C50E0" : "2px solid transparent",
              background: "none",
              borderLeft: "none",
              borderRight: "none",
              borderTop: "none",
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              gap: "8px",
            }}
          >
            <Sliders size={17} />
            Chọn Model Theo Nhiệm Vụ (Routing)
          </button>
        </div>

        {/* ── TAB 1: Analytics & Token Statistics ── */}
        {activeTab === "analytics" && (
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "24px" }}>
            {/* Daily Token & Cost Usage Trend */}
            <div
              style={{
                background: "#FFFFFF",
                borderRadius: "14px",
                padding: "24px",
                border: "1px solid #E2E8F0",
                gridColumn: "span 2",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
                <div>
                  <h3 style={{ fontSize: "1.05rem", fontWeight: 700, color: "#0F172A", margin: 0 }}>
                    Xu Hướng Tiêu Thụ Token & Chi Phí Theo Ngày
                  </h3>
                  <p style={{ fontSize: "0.8rem", color: "#64748B", margin: "2px 0 0 0" }}>
                    Phân tách giữa Prompt Tokens (Input) và Completion Tokens (Output).
                  </p>
                </div>
              </div>

              <div style={{ width: "100%", height: 320 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={tokenStats.daily_trends || []}>
                    <defs>
                      <linearGradient id="promptGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#3C50E0" stopOpacity={0.4} />
                        <stop offset="95%" stopColor="#3C50E0" stopOpacity={0} />
                      </linearGradient>
                      <linearGradient id="completionGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#10B981" stopOpacity={0.4} />
                        <stop offset="95%" stopColor="#10B981" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#E2E8F0" />
                    <XAxis dataKey="date" stroke="#94A3B8" fontSize={12} />
                    <YAxis stroke="#94A3B8" fontSize={12} />
                    <Tooltip
                      contentStyle={{ background: "#1E293B", borderRadius: "8px", border: "none", color: "#FFF" }}
                    />
                    <Legend />
                    <Area type="monotone" dataKey="prompt_tokens" name="Input Tokens (Prompt)" stroke="#3C50E0" fillOpacity={1} fill="url(#promptGrad)" />
                    <Area type="monotone" dataKey="completion_tokens" name="Output Tokens (Completion)" stroke="#10B981" fillOpacity={1} fill="url(#completionGrad)" />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </div>

            {/* Model Distribution Chart */}
            <div style={{ background: "#FFFFFF", borderRadius: "14px", padding: "24px", border: "1px solid #E2E8F0" }}>
              <h3 style={{ fontSize: "1.05rem", fontWeight: 700, color: "#0F172A", margin: "0 0 16px 0" }}>
                Tỷ Lệ Tiêu Thụ Theo LLM Model
              </h3>
              <div style={{ width: "100%", height: 260 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie
                      data={tokenStats.model_distribution || []}
                      dataKey="cost_usd"
                      nameKey="model_name"
                      cx="50%"
                      cy="50%"
                      outerRadius={85}
                      label={({ name, percent }) => `${name} (${((percent ?? 0) * 100).toFixed(0)}%)`}
                    >
                      {tokenStats.model_distribution.map((entry, index) => (
                        <Cell key={`cell-${index}`} fill={MODEL_COLORS[entry.model_name] || MODEL_COLORS.default} />
                      ))}
                    </Pie>
                    <Tooltip />
                  </PieChart>
                </ResponsiveContainer>
              </div>
            </div>

            {/* Token Efficiency Breakdown Table */}
            <div style={{ background: "#FFFFFF", borderRadius: "14px", padding: "24px", border: "1px solid #E2E8F0" }}>
              <h3 style={{ fontSize: "1.05rem", fontWeight: 700, color: "#0F172A", margin: "0 0 16px 0" }}>
                Thống Kê Chi Tiết Model
              </h3>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.85rem" }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid #E2E8F0", color: "#64748B", textAlign: "left" }}>
                    <th style={{ padding: "8px 4px" }}>Model</th>
                    <th style={{ padding: "8px 4px" }}>Số Yêu Cầu</th>
                    <th style={{ padding: "8px 4px" }}>Tổng Token</th>
                    <th style={{ padding: "8px 4px", textAlign: "right" }}>Chi Phí (USD)</th>
                  </tr>
                </thead>
                <tbody>
                  {tokenStats.model_distribution.map((m) => (
                    <tr key={m.model_name} style={{ borderBottom: "1px solid #F1F5F9" }}>
                      <td style={{ padding: "10px 4px", fontWeight: 600, display: "flex", alignItems: "center", gap: "6px" }}>
                        <span style={{ width: "8px", height: "8px", borderRadius: "50%", background: MODEL_COLORS[m.model_name] || "#64748B" }} />
                        {m.model_name}
                      </td>
                      <td style={{ padding: "10px 4px" }}>{m.requests}</td>
                      <td style={{ padding: "10px 4px" }}>{(m.prompt_tokens + m.completion_tokens).toLocaleString()}</td>
                      <td style={{ padding: "10px 4px", textAlign: "right", fontWeight: 700, color: "#0F172A" }}>
                        ${m.cost_usd.toFixed(4)}
                      </td>
                    </tr>
                  ))}
                  {tokenStats.model_distribution.length === 0 && (
                    <tr>
                      <td colSpan={4} style={{ padding: "20px", textAlign: "center", color: "#94A3B8" }}>
                        Chưa có usage model trong kỳ.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* ── TAB 2: Cost Breakdowns (4 Dimensions) ── */}
        {activeTab === "breakdowns" && (
          <div>
            {/* Sub-selector buttons */}
            <div style={{ display: "flex", gap: "10px", marginBottom: "20px" }}>
              <button
                onClick={() => setBreakdownType("agent")}
                style={{
                  padding: "8px 16px",
                  borderRadius: "8px",
                  fontSize: "0.85rem",
                  fontWeight: 600,
                  background: breakdownType === "agent" ? "#3C50E0" : "#FFFFFF",
                  color: breakdownType === "agent" ? "#FFFFFF" : "#475569",
                  border: "1px solid #E2E8F0",
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  gap: "6px",
                }}
              >
                <Bot size={15} />
                Chi Phí Theo Agent
              </button>

              <button
                onClick={() => setBreakdownType("employee")}
                style={{
                  padding: "8px 16px",
                  borderRadius: "8px",
                  fontSize: "0.85rem",
                  fontWeight: 600,
                  background: breakdownType === "employee" ? "#3C50E0" : "#FFFFFF",
                  color: breakdownType === "employee" ? "#FFFFFF" : "#475569",
                  border: "1px solid #E2E8F0",
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  gap: "6px",
                }}
              >
                <Users size={15} />
                Chi Phí Theo Nhân Viên
              </button>

              <button
                onClick={() => setBreakdownType("department")}
                style={{
                  padding: "8px 16px",
                  borderRadius: "8px",
                  fontSize: "0.85rem",
                  fontWeight: 600,
                  background: breakdownType === "department" ? "#3C50E0" : "#FFFFFF",
                  color: breakdownType === "department" ? "#FFFFFF" : "#475569",
                  border: "1px solid #E2E8F0",
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  gap: "6px",
                }}
              >
                <Building2 size={15} />
                Chi Phí Theo Phòng Ban
              </button>

              <button
                onClick={() => setBreakdownType("workflow")}
                style={{
                  padding: "8px 16px",
                  borderRadius: "8px",
                  fontSize: "0.85rem",
                  fontWeight: 600,
                  background: breakdownType === "workflow" ? "#3C50E0" : "#FFFFFF",
                  color: breakdownType === "workflow" ? "#FFFFFF" : "#475569",
                  border: "1px solid #E2E8F0",
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  gap: "6px",
                }}
              >
                <Layers size={15} />
                Chi Phí Theo Workflow
              </button>
            </div>

            {/* Content Table based on selected breakdownType */}
            <div style={{ background: "#FFFFFF", borderRadius: "14px", padding: "24px", border: "1px solid #E2E8F0" }}>
              {(
                (breakdownType === "agent" && agentCosts.length === 0) ||
                (breakdownType === "employee" && employeeCosts.length === 0) ||
                (breakdownType === "department" && deptCosts.length === 0) ||
                (breakdownType === "workflow" && workflowCosts.length === 0)
              ) && (
                <div style={{ padding: "12px 0", color: "#94A3B8", fontSize: "0.85rem" }}>
                  Chưa có dữ liệu chi phí cho chiều phân tích này trong kỳ.
                </div>
              )}
              {/* 1. Agent Breakdown */}
              {breakdownType === "agent" && (
                <div>
                  <h3 style={{ fontSize: "1.05rem", fontWeight: 700, color: "#0F172A", marginBottom: "16px" }}>
                    Bảng Thống Kê Chi Phí Theo AI Agent Employee
                  </h3>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.875rem" }}>
                    <thead>
                      <tr style={{ borderBottom: "1px solid #E2E8F0", color: "#64748B", textAlign: "left" }}>
                        <th style={{ padding: "12px" }}>Agent Role</th>
                        <th style={{ padding: "12px" }}>Số Yêu Cầu</th>
                        <th style={{ padding: "12px" }}>Prompt Tokens (Input)</th>
                        <th style={{ padding: "12px" }}>Completion Tokens (Output)</th>
                        <th style={{ padding: "12px" }}>Tổng Tokens</th>
                        <th style={{ padding: "12px", textAlign: "right" }}>Tổng Chi Phí (USD)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {agentCosts.map((item) => (
                        <tr key={item.agent_role} style={{ borderBottom: "1px solid #F1F5F9" }}>
                          <td style={{ padding: "14px 12px", fontWeight: 700, display: "flex", alignItems: "center", gap: "8px" }}>
                            <span style={{ fontSize: "1.2rem" }}>{AGENT_EMOJIS[item.agent_role] || "🤖"}</span>
                            {item.agent_role} Agent
                          </td>
                          <td style={{ padding: "14px 12px" }}>{item.requests}</td>
                          <td style={{ padding: "14px 12px", color: "#3C50E0" }}>{item.prompt_tokens.toLocaleString()}</td>
                          <td style={{ padding: "14px 12px", color: "#10B981" }}>{item.completion_tokens.toLocaleString()}</td>
                          <td style={{ padding: "14px 12px", fontWeight: 600 }}>{item.total_tokens.toLocaleString()}</td>
                          <td style={{ padding: "14px 12px", textAlign: "right", fontWeight: 800, color: "#0F172A" }}>
                            ${item.total_cost_usd.toFixed(4)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* 2. Employee Breakdown */}
              {breakdownType === "employee" && (
                <div>
                  <h3 style={{ fontSize: "1.05rem", fontWeight: 700, color: "#0F172A", marginBottom: "16px" }}>
                    Bảng Thống Kê Chi Phí Theo Nhân Viên (Users)
                  </h3>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.875rem" }}>
                    <thead>
                      <tr style={{ borderBottom: "1px solid #E2E8F0", color: "#64748B", textAlign: "left" }}>
                        <th style={{ padding: "12px" }}>Nhân Viên</th>
                        <th style={{ padding: "12px" }}>Email</th>
                        <th style={{ padding: "12px" }}>Phòng Ban</th>
                        <th style={{ padding: "12px" }}>Số Yêu Cầu</th>
                        <th style={{ padding: "12px" }}>Tổng Tokens</th>
                        <th style={{ padding: "12px", textAlign: "right" }}>Tổng Chi Phí (USD)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {employeeCosts.map((item) => (
                        <tr key={item.user_id} style={{ borderBottom: "1px solid #F1F5F9" }}>
                          <td style={{ padding: "14px 12px", fontWeight: 700 }}>{item.full_name}</td>
                          <td style={{ padding: "14px 12px", color: "#64748B" }}>{item.email}</td>
                          <td style={{ padding: "14px 12px" }}>
                            <span style={{ padding: "3px 8px", borderRadius: "6px", background: "#F1F5F9", fontSize: "0.78rem", fontWeight: 600 }}>
                              {item.department}
                            </span>
                          </td>
                          <td style={{ padding: "14px 12px" }}>{item.requests}</td>
                          <td style={{ padding: "14px 12px", fontWeight: 600 }}>{item.total_tokens.toLocaleString()}</td>
                          <td style={{ padding: "14px 12px", textAlign: "right", fontWeight: 800, color: "#0F172A" }}>
                            ${item.total_cost_usd.toFixed(4)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* 3. Department Breakdown */}
              {breakdownType === "department" && (
                <div>
                  <h3 style={{ fontSize: "1.05rem", fontWeight: 700, color: "#0F172A", marginBottom: "16px" }}>
                    Bảng Thống Kê Chi Phí Theo Phòng Ban (Department)
                  </h3>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.875rem" }}>
                    <thead>
                      <tr style={{ borderBottom: "1px solid #E2E8F0", color: "#64748B", textAlign: "left" }}>
                        <th style={{ padding: "12px" }}>Phòng Ban</th>
                        <th style={{ padding: "12px" }}>Số Yêu Cầu</th>
                        <th style={{ padding: "12px" }}>Input Tokens</th>
                        <th style={{ padding: "12px" }}>Output Tokens</th>
                        <th style={{ padding: "12px" }}>Tổng Tokens</th>
                        <th style={{ padding: "12px", textAlign: "right" }}>Chi Phí (USD)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {deptCosts.map((item) => (
                        <tr key={item.department} style={{ borderBottom: "1px solid #F1F5F9" }}>
                          <td style={{ padding: "14px 12px", fontWeight: 700 }}>Phòng {item.department}</td>
                          <td style={{ padding: "14px 12px" }}>{item.requests}</td>
                          <td style={{ padding: "14px 12px", color: "#3C50E0" }}>{item.prompt_tokens.toLocaleString()}</td>
                          <td style={{ padding: "14px 12px", color: "#10B981" }}>{item.completion_tokens.toLocaleString()}</td>
                          <td style={{ padding: "14px 12px", fontWeight: 600 }}>{item.total_tokens.toLocaleString()}</td>
                          <td style={{ padding: "14px 12px", textAlign: "right", fontWeight: 800, color: "#0F172A" }}>
                            ${item.total_cost_usd.toFixed(4)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* 4. Workflow Breakdown */}
              {breakdownType === "workflow" && (
                <div>
                  <h3 style={{ fontSize: "1.05rem", fontWeight: 700, color: "#0F172A", marginBottom: "16px" }}>
                    Bảng Thống Kê Chi Phí Theo Workflow Session
                  </h3>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.875rem" }}>
                    <thead>
                      <tr style={{ borderBottom: "1px solid #E2E8F0", color: "#64748B", textAlign: "left" }}>
                        <th style={{ padding: "12px" }}>Tên Workflow</th>
                        <th style={{ padding: "12px" }}>Trạng Thái</th>
                        <th style={{ padding: "12px" }}>Số Yêu Cầu</th>
                        <th style={{ padding: "12px" }}>Tổng Tokens</th>
                        <th style={{ padding: "12px", textAlign: "right" }}>Chi Phí (USD)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {workflowCosts.map((item) => (
                        <tr key={item.workflow_id} style={{ borderBottom: "1px solid #F1F5F9" }}>
                          <td style={{ padding: "14px 12px", fontWeight: 700 }}>{item.title}</td>
                          <td style={{ padding: "14px 12px" }}>
                            <span
                              style={{
                                padding: "3px 8px",
                                borderRadius: "12px",
                                background: item.status === "COMPLETED" ? "#D1FAE5" : "#FEF3C7",
                                color: item.status === "COMPLETED" ? "#059669" : "#D97706",
                                fontSize: "0.75rem",
                                fontWeight: 700,
                              }}
                            >
                              {item.status}
                            </span>
                          </td>
                          <td style={{ padding: "14px 12px" }}>{item.requests}</td>
                          <td style={{ padding: "14px 12px", fontWeight: 600 }}>{item.total_tokens.toLocaleString()}</td>
                          <td style={{ padding: "14px 12px", textAlign: "right", fontWeight: 800, color: "#0F172A" }}>
                            ${item.total_cost_usd.toFixed(4)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        )}

        {/* ── TAB 3: Budgets & Threshold Alerts ── */}
        {activeTab === "budgets" && (
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "20px" }}>
              <div>
                <h3 style={{ fontSize: "1.1rem", fontWeight: 700, color: "#0F172A", margin: 0 }}>
                  Cấu Hình Ngân Sách Hàng Tháng & Ngưỡng Cảnh Báo
                </h3>
                <p style={{ fontSize: "0.85rem", color: "#64748B", margin: "2px 0 0 0" }}>
                  Thiết lập hạn mức chi tiêu token cho toàn hệ thống, phòng ban hoặc từng agent.
                </p>
              </div>

              <button
                onClick={() => {
                  setEditingBudget("new");
                  setBudgetForm({
                    scope_type: "DEPARTMENT",
                    scope_id: "HR",
                    monthly_budget_usd: 100,
                    alert_threshold_pct: 80,
                    is_active: true,
                  });
                }}
                style={{
                  padding: "9px 16px",
                  borderRadius: "8px",
                  background: "#3C50E0",
                  color: "#FFFFFF",
                  fontWeight: 600,
                  fontSize: "0.85rem",
                  border: "none",
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  gap: "6px",
                }}
              >
                <Plus size={16} />
                Thêm Cấu Hình Ngân Sách
              </button>
            </div>

            {/* Budget Cards Grid */}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: "18px", marginBottom: "32px" }}>
              {budgetsAlerts.budgets.map((b) => (
                <div
                  key={b.id}
                  style={{
                    background: "#FFFFFF",
                    borderRadius: "14px",
                    padding: "20px",
                    border: `1.5px solid ${b.status === "EXCEEDED" ? "#EF4444" : b.status === "WARNING" ? "#F59E0B" : "#E2E8F0"}`,
                    boxShadow: "0 2px 4px rgba(0,0,0,0.02)",
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
                    <span style={{ fontSize: "0.8rem", fontWeight: 700, padding: "3px 8px", borderRadius: "6px", background: "#F1F5F9" }}>
                      {b.scope_type}: {b.scope_id}
                    </span>
                    <span
                      style={{
                        fontSize: "0.75rem",
                        fontWeight: 700,
                        color: b.status === "EXCEEDED" ? "#DC2626" : b.status === "WARNING" ? "#D97706" : "#059669",
                      }}
                    >
                      {b.status} ({b.usage_pct}%)
                    </span>
                  </div>

                  <div style={{ fontSize: "1.4rem", fontWeight: 800, color: "#0F172A", marginBottom: "6px" }}>
                    ${b.current_spend_usd.toFixed(2)}
                    <span style={{ fontSize: "0.85rem", fontWeight: 500, color: "#94A3B8", marginLeft: "6px" }}>
                      / ${b.monthly_budget_usd}
                    </span>
                  </div>

                  <div style={{ width: "100%", height: "6px", background: "#F1F5F9", borderRadius: "10px", overflow: "hidden", marginBottom: "12px" }}>
                    <div
                      style={{
                        width: `${Math.min(b.usage_pct, 100)}%`,
                        height: "100%",
                        background: b.status === "EXCEEDED" ? "#EF4444" : b.status === "WARNING" ? "#F59E0B" : "#10B981",
                        borderRadius: "10px",
                      }}
                    />
                  </div>

                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: "0.78rem", color: "#64748B" }}>
                    <span>Ngưỡng cảnh báo: <b>{b.alert_threshold_pct}%</b></span>
                    <button
                      onClick={() => {
                        setEditingBudget(b);
                        setBudgetForm({
                          scope_type: b.scope_type,
                          scope_id: b.scope_id,
                          monthly_budget_usd: b.monthly_budget_usd,
                          alert_threshold_pct: b.alert_threshold_pct,
                          is_active: b.is_active,
                        });
                      }}
                      style={{ color: "#3C50E0", background: "none", border: "none", cursor: "pointer", fontWeight: 600 }}
                    >
                      Chỉnh sửa
                    </button>
                  </div>
                </div>
              ))}
              {budgetsAlerts.budgets.length === 0 && (
                <div style={{ color: "#94A3B8", fontSize: "0.85rem" }}>
                  Chưa có hạn mức. Dùng nút “Thêm Cấu Hình Ngân Sách” để tạo hạn mức đầu tiên.
                </div>
              )}
            </div>

            {/* Active Alert Trail */}
            <div style={{ background: "#FFFFFF", borderRadius: "14px", padding: "24px", border: "1px solid #E2E8F0" }}>
              <h3 style={{ fontSize: "1.05rem", fontWeight: 700, color: "#0F172A", marginBottom: "16px" }}>
                Nhật Ký Cảnh Báo Vượt Ngưỡng Ngân Sách
              </h3>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.85rem" }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid #E2E8F0", color: "#64748B", textAlign: "left" }}>
                    <th style={{ padding: "10px" }}>Mức Độ</th>
                    <th style={{ padding: "10px" }}>Tiêu Đề Cảnh Báo</th>
                    <th style={{ padding: "10px" }}>Nội Dung Chi Tiết</th>
                    <th style={{ padding: "10px" }}>Thời Gian</th>
                  </tr>
                </thead>
                <tbody>
                  {budgetsAlerts.alerts.map((alt) => (
                    <tr key={alt.id} style={{ borderBottom: "1px solid #F1F5F9" }}>
                      <td style={{ padding: "12px 10px" }}>
                        <span
                          style={{
                            padding: "3px 8px",
                            borderRadius: "12px",
                            fontSize: "0.75rem",
                            fontWeight: 700,
                            background: alt.severity === "HIGH" ? "#FEE2E2" : "#FEF3C7",
                            color: alt.severity === "HIGH" ? "#DC2626" : "#D97706",
                          }}
                        >
                          {alt.severity}
                        </span>
                      </td>
                      <td style={{ padding: "12px 10px", fontWeight: 700 }}>{alt.title}</td>
                      <td style={{ padding: "12px 10px", color: "#475569" }}>{alt.message}</td>
                      <td style={{ padding: "12px 10px", color: "#94A3B8" }}>{new Date(alt.timestamp).toLocaleTimeString("vi-VN")}</td>
                    </tr>
                  ))}
                  {(!budgetsAlerts.alerts || budgetsAlerts.alerts.length === 0) && (
                    <tr>
                      <td colSpan={4} style={{ padding: "20px", textAlign: "center", color: "#94A3B8" }}>
                        Không có cảnh báo vượt ngưỡng nào hiện tại. Hệ thống hoạt động trong tầm kiểm soát.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* ── TAB 4: Model Routing per Task ── */}
        {activeTab === "routing" && (
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "20px" }}>
              <div>
                <h3 style={{ fontSize: "1.1rem", fontWeight: 700, color: "#0F172A", margin: 0 }}>
                  Cấu Hình Điều Hướng Model Theo Nhiệm Vụ (Task-Model Routing)
                </h3>
                <p style={{ fontSize: "0.85rem", color: "#64748B", margin: "2px 0 0 0" }}>
                  Chọn model đã có bảng giá được kiểm soát; rule này được resolver backend dùng trước khi gọi provider.
                </p>
              </div>

              <button
                onClick={() => {
                  setEditingRule("new");
                  setRuleForm({ task_type: "CUSTOM_TASK", agent_role: "HR", preferred_model: "gpt-3.5-turbo", fallback_model: "gpt-4o", max_tokens: 2048, cost_saving_strategy: "LOW_COST", is_active: true });
                }}
                style={{
                  padding: "9px 16px",
                  borderRadius: "8px",
                  background: "#3C50E0",
                  color: "#FFFFFF",
                  fontWeight: 600,
                  fontSize: "0.85rem",
                  border: "none",
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  gap: "6px",
                }}
              >
                <Plus size={16} />
                Thêm Quy Tắc Routing
              </button>
            </div>

            {/* Routing Rules Table */}
            <div style={{ background: "#FFFFFF", borderRadius: "14px", padding: "24px", border: "1px solid #E2E8F0" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.875rem" }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid #E2E8F0", color: "#64748B", textAlign: "left" }}>
                    <th style={{ padding: "12px" }}>Loại Nhiệm Vụ (Task Type)</th>
                    <th style={{ padding: "12px" }}>Agent Phụ Trách</th>
                    <th style={{ padding: "12px" }}>Model Tiêu Chuẩn</th>
                    <th style={{ padding: "12px" }}>Model Dự Phòng (Fallback)</th>
                    <th style={{ padding: "12px" }}>Max Tokens</th>
                    <th style={{ padding: "12px" }}>Chiến Lược Tối Ưu</th>
                    <th style={{ padding: "12px" }}>Trạng Thái</th>
                    <th style={{ padding: "12px", textAlign: "right" }}>Thao Tác</th>
                  </tr>
                </thead>
                <tbody>
                  {routingRules.map((rule) => (
                    <tr key={rule.id} style={{ borderBottom: "1px solid #F1F5F9" }}>
                      <td style={{ padding: "14px 12px", fontWeight: 700 }}>{rule.task_type}</td>
                      <td style={{ padding: "14px 12px" }}>{rule.agent_role}</td>
                      <td style={{ padding: "14px 12px" }}>
                        <span style={{ padding: "3px 8px", borderRadius: "6px", background: "#EEF2FF", color: "#3C50E0", fontWeight: 700, fontSize: "0.78rem" }}>
                          {rule.preferred_model}
                        </span>
                      </td>
                      <td style={{ padding: "14px 12px" }}>
                        <span style={{ padding: "3px 8px", borderRadius: "6px", background: "#F1F5F9", color: "#64748B", fontSize: "0.78rem" }}>
                          {rule.fallback_model}
                        </span>
                      </td>
                      <td style={{ padding: "14px 12px" }}>{rule.max_tokens}</td>
                      <td style={{ padding: "14px 12px" }}>
                        <span style={{ padding: "3px 8px", borderRadius: "12px", background: "#D1FAE5", color: "#059669", fontSize: "0.75rem", fontWeight: 700 }}>
                          {rule.cost_saving_strategy}
                        </span>
                      </td>
                      <td style={{ padding: "14px 12px" }}>
                        <span style={{ fontSize: "0.8rem", fontWeight: 700, color: rule.is_active ? "#10B981" : "#94A3B8" }}>
                          {rule.is_active ? "Đang Bật" : "Tắt"}
                        </span>
                      </td>
                      <td style={{ padding: "14px 12px", textAlign: "right" }}>
                        <button
                          onClick={() => {
                            setEditingRule(rule);
                            setRuleForm({ ...rule });
                          }}
                          style={{ color: "#3C50E0", background: "none", border: "none", cursor: "pointer", fontWeight: 600 }}
                        >
                          Sửa
                        </button>
                      </td>
                    </tr>
                  ))}
                  {routingRules.length === 0 && (
                    <tr>
                      <td colSpan={8} style={{ padding: "20px", textAlign: "center", color: "#94A3B8" }}>
                        Chưa có routing rule. Chỉ các model có bảng giá hợp lệ mới có thể được chọn.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      {/* ── Modal Update Budget ── */}
      {editingBudget !== null && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(15,23,42,0.5)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100 }}>
          <div style={{ background: "#FFFFFF", borderRadius: "14px", padding: "28px", width: "420px", boxShadow: "0 20px 25px -5px rgba(0,0,0,0.1)" }}>
            <h3 style={{ fontSize: "1.1rem", fontWeight: 700, margin: "0 0 16px 0" }}>Cài Đặt Ngân Sách Hàng Tháng</h3>

            <div style={{ marginBottom: "14px" }}>
              <label style={{ display: "block", fontSize: "0.8rem", fontWeight: 600, color: "#475569", marginBottom: "4px" }}>Phạm Vi (Scope Type)</label>
              <select
                value={budgetForm.scope_type}
                onChange={(e) => setBudgetForm({ ...budgetForm, scope_type: e.target.value as BudgetScope })}
                style={{ width: "100%", padding: "8px 12px", borderRadius: "8px", border: "1px solid #CBD5E1" }}
              >
                <option value="TENANT">Toàn Doanh Nghiệp (TENANT)</option>
                <option value="DEPARTMENT">Theo Phòng Ban (DEPARTMENT)</option>
                <option value="AGENT">Theo Agent (AGENT)</option>
              </select>
            </div>

            <div style={{ marginBottom: "14px" }}>
              <label style={{ display: "block", fontSize: "0.8rem", fontWeight: 600, color: "#475569", marginBottom: "4px" }}>Tên Phòng / Agent (Scope ID)</label>
              <input
                type="text"
                value={budgetForm.scope_id}
                onChange={(e) => setBudgetForm({ ...budgetForm, scope_id: e.target.value })}
                style={{ width: "100%", padding: "8px 12px", borderRadius: "8px", border: "1px solid #CBD5E1" }}
                placeholder="VD: HR, LEGAL, IT, CEO, ALL"
              />
            </div>

            <div style={{ marginBottom: "14px" }}>
              <label style={{ display: "block", fontSize: "0.8rem", fontWeight: 600, color: "#475569", marginBottom: "4px" }}>Ngân Sách Tháng (USD)</label>
              <input
                type="number"
                value={budgetForm.monthly_budget_usd}
                onChange={(e) => setBudgetForm({ ...budgetForm, monthly_budget_usd: parseFloat(e.target.value) || 0 })}
                style={{ width: "100%", padding: "8px 12px", borderRadius: "8px", border: "1px solid #CBD5E1" }}
              />
            </div>

            <div style={{ marginBottom: "20px" }}>
              <label style={{ display: "block", fontSize: "0.8rem", fontWeight: 600, color: "#475569", marginBottom: "4px" }}>Ngưỡng Cảnh Báo (%)</label>
              <input
                type="number"
                value={budgetForm.alert_threshold_pct}
                onChange={(e) => setBudgetForm({ ...budgetForm, alert_threshold_pct: parseInt(e.target.value) || 80 })}
                style={{ width: "100%", padding: "8px 12px", borderRadius: "8px", border: "1px solid #CBD5E1" }}
              />
            </div>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px" }}>
              <button
                onClick={() => setEditingBudget(null)}
                style={{ padding: "8px 16px", borderRadius: "8px", background: "#F1F5F9", color: "#475569", border: "none", cursor: "pointer", fontWeight: 600 }}
              >
                Hủy
              </button>
              <button
                onClick={handleSaveBudget}
                style={{ padding: "8px 16px", borderRadius: "8px", background: "#3C50E0", color: "#FFFFFF", border: "none", cursor: "pointer", fontWeight: 600 }}
              >
                Lưu Thay Đổi
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Modal Update Model Routing Rule ── */}
      {editingRule !== null && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(15,23,42,0.5)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100 }}>
          <div style={{ background: "#FFFFFF", borderRadius: "14px", padding: "28px", width: "450px", boxShadow: "0 20px 25px -5px rgba(0,0,0,0.1)" }}>
            <h3 style={{ fontSize: "1.1rem", fontWeight: 700, margin: "0 0 16px 0" }}>Quy Tắc Chọn Model Theo Nhiệm Vụ</h3>

            <div style={{ marginBottom: "14px" }}>
              <label style={{ display: "block", fontSize: "0.8rem", fontWeight: 600, color: "#475569", marginBottom: "4px" }}>Loại Nhiệm Vụ (Task Type)</label>
              <input
                type="text"
                value={ruleForm.task_type}
                onChange={(e) => setRuleForm({ ...ruleForm, task_type: e.target.value })}
                style={{ width: "100%", padding: "8px 12px", borderRadius: "8px", border: "1px solid #CBD5E1" }}
                placeholder="VD: HR_FAQ, LEGAL_REVIEW"
              />
            </div>

            <div style={{ marginBottom: "14px" }}>
              <label style={{ display: "block", fontSize: "0.8rem", fontWeight: 600, color: "#475569", marginBottom: "4px" }}>Model Ưu Tiên (Preferred Model)</label>
              <select
                value={ruleForm.preferred_model}
                onChange={(e) => setRuleForm({ ...ruleForm, preferred_model: e.target.value })}
                style={{ width: "100%", padding: "8px 12px", borderRadius: "8px", border: "1px solid #CBD5E1" }}
              >
                <option value="gpt-3.5-turbo">gpt-3.5-turbo (Rẻ, nhanh)</option>
                <option value="gemini-2.5-flash">gemini-2.5-flash (Tiết kiệm)</option>
                <option value="gpt-4o">gpt-4o (Thông minh, chuẩn doanh nghiệp)</option>
                <option value="claude-sonnet-4">claude-sonnet-4 (Pháp lý, bài viết dài)</option>
              </select>
            </div>

            <div style={{ marginBottom: "14px" }}>
              <label style={{ display: "block", fontSize: "0.8rem", fontWeight: 600, color: "#475569", marginBottom: "4px" }}>Model Dự Phòng (Fallback Model)</label>
              <select
                value={ruleForm.fallback_model}
                onChange={(e) => setRuleForm({ ...ruleForm, fallback_model: e.target.value })}
                style={{ width: "100%", padding: "8px 12px", borderRadius: "8px", border: "1px solid #CBD5E1" }}
              >
                <option value="gpt-4o">gpt-4o</option>
                <option value="claude-sonnet-4">claude-sonnet-4</option>
                <option value="gemini-2.5-flash">gemini-2.5-flash</option>
                <option value="gpt-3.5-turbo">gpt-3.5-turbo</option>
              </select>
            </div>

            <div style={{ marginBottom: "20px" }}>
              <label style={{ display: "block", fontSize: "0.8rem", fontWeight: 600, color: "#475569", marginBottom: "4px" }}>Max Tokens Giới Hạn</label>
              <input
                type="number"
                value={ruleForm.max_tokens}
                onChange={(e) => setRuleForm({ ...ruleForm, max_tokens: parseInt(e.target.value) || 2048 })}
                style={{ width: "100%", padding: "8px 12px", borderRadius: "8px", border: "1px solid #CBD5E1" }}
              />
            </div>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px" }}>
              <button
                onClick={() => setEditingRule(null)}
                style={{ padding: "8px 16px", borderRadius: "8px", background: "#F1F5F9", color: "#475569", border: "none", cursor: "pointer", fontWeight: 600 }}
              >
                Hủy
              </button>
              <button
                onClick={handleSaveRoutingRule}
                style={{ padding: "8px 16px", borderRadius: "8px", background: "#3C50E0", color: "#FFFFFF", border: "none", cursor: "pointer", fontWeight: 600 }}
              >
                Lưu Quy Tắc
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
