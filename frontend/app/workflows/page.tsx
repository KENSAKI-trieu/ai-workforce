"use client";

import axios from "axios";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Bot, Clock, GitBranch, Play, Plus, RefreshCw, ShieldAlert, Zap } from "lucide-react";
import Sidebar from "@/components/Sidebar";
import api from "@/lib/api";
import { useAuthStore } from "@/store/useAuthStore";

interface WorkflowNode {
  id: string;
  type: string;
  name: string;
  config?: Record<string, unknown>;
  next?: string[];
}

interface WorkflowItem {
  id: string;
  title: string;
  description?: string;
  trigger_type: string;
  nodes: WorkflowNode[];
  status: string;
  is_active: boolean;
  created_at: string;
}

interface WorkflowRun extends WorkflowItem {
  definition_id?: string;
  current_step: number;
  completed_at?: string;
}

function messageFrom(error: unknown) {
  return axios.isAxiosError(error)
    ? String(error.response?.data?.detail || error.message)
    : "Không thể xử lý yêu cầu.";
}

export default function WorkflowsPage() {
  const router = useRouter();
  const { isAuthenticated, hasHydrated } = useAuthStore();
  const [workflows, setWorkflows] = useState<WorkflowItem[]>([]);
  const [selected, setSelected] = useState<WorkflowItem | null>(null);
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [title, setTitle] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchWorkflows = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const { data } = await api.get<WorkflowItem[]>("/api/v1/workflows");
      setWorkflows(data);
      setSelected((current) =>
        current ? data.find((item) => item.id === current.id) || data[0] || null : data[0] || null,
      );
    } catch (reason) {
      setError(messageFrom(reason));
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchRuns = useCallback(async (workflowId: string) => {
    try {
      const { data } = await api.get<WorkflowRun[]>(`/api/v1/workflows/${workflowId}/runs`);
      setRuns(data);
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
    const timer = window.setTimeout(() => void fetchWorkflows(), 0);
    return () => window.clearTimeout(timer);
  }, [fetchWorkflows, hasHydrated, isAuthenticated, router]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      if (selected) void fetchRuns(selected.id);
      else setRuns([]);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [fetchRuns, selected]);

  const createWorkflow = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    try {
      await api.post("/api/v1/workflows", {
        title,
        description: "Luồng mẫu có cổng phê duyệt con người trước đầu ra.",
        trigger_type: "MANUAL",
        nodes: [
          { id: "trigger", type: "TRIGGER", name: "Nhân viên bấm chạy", next: ["agent"] },
          { id: "agent", type: "AI_AGENT", name: "AI xử lý yêu cầu", next: ["approval"] },
          {
            id: "approval",
            type: "HUMAN_APPROVAL",
            name: "Quản lý phê duyệt",
            config: { risk_level: "HIGH", action_type: "PUBLISH_AI_OUTPUT" },
            next: ["output"],
          },
          { id: "output", type: "OUTPUT", name: "Lưu kết quả" },
        ],
      });
      setTitle("");
      setShowCreate(false);
      await fetchWorkflows();
    } catch (reason) {
      setError(messageFrom(reason));
    }
  };

  const runWorkflow = async () => {
    if (!selected) return;
    setRunning(true);
    setError(null);
    try {
      await api.post(`/api/v1/workflows/${selected.id}/run`);
      await fetchRuns(selected.id);
    } catch (reason) {
      setError(messageFrom(reason));
    } finally {
      setRunning(false);
    }
  };

  if (!hasHydrated || !isAuthenticated) return null;

  const nodeIcon = (type: string) => {
    if (type === "TRIGGER") return <Zap size={17} />;
    if (type === "AI_AGENT") return <Bot size={17} />;
    if (type === "HUMAN_APPROVAL") return <ShieldAlert size={17} />;
    return <GitBranch size={17} />;
  };

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "var(--body-bg)" }}>
      <Sidebar />
      <div style={{ flex: 1, minWidth: 0 }}>
        <header className="ta-topbar">
          <div className="breadcrumb"><span>Home</span><span className="breadcrumb-sep">›</span><span className="breadcrumb-current">Workflow Automation</span></div>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="ta-btn ta-btn-ghost" onClick={() => void fetchWorkflows()}><RefreshCw size={15} /> Làm mới</button>
            <button className="ta-btn ta-btn-primary" onClick={() => setShowCreate(true)}><Plus size={15} /> Tạo workflow</button>
          </div>
        </header>
        <main className="page-main" style={{ padding: "24px 32px" }}>
          <h1 style={{ fontSize: "1.5rem", fontWeight: 800 }}>Workflow Automation</h1>
          <p style={{ color: "var(--text-muted)", margin: "6px 0 20px" }}>
            Xây chuỗi Trigger → AI Agent → Human Approval → Output và theo dõi từng lần chạy.
          </p>
          {error && <div className="ta-card" style={{ color: "#B91C1C", padding: 14, marginBottom: 14 }}>{error}</div>}

          <div className="stack-mobile" style={{ display: "grid", gridTemplateColumns: "320px minmax(480px, 1fr)", gap: 20 }}>
            <section>
              {loading ? <div className="ta-card" style={{ padding: 20 }}>Đang tải...</div> : workflows.map((workflow) => (
                <button
                  key={workflow.id}
                  className="ta-card"
                  onClick={() => setSelected(workflow)}
                  style={{ width: "100%", textAlign: "left", padding: 15, marginBottom: 10, borderLeft: selected?.id === workflow.id ? "4px solid var(--primary)" : undefined }}
                >
                  <strong>{workflow.title}</strong>
                  <div style={{ color: "var(--text-muted)", fontSize: 12, marginTop: 4 }}>{workflow.trigger_type} · {workflow.nodes.length} node</div>
                </button>
              ))}
              {!loading && workflows.length === 0 && <div className="ta-card" style={{ padding: 20 }}>Chưa có workflow.</div>}
            </section>

            <section>
              {selected ? (
                <>
                  <div className="ta-card" style={{ padding: 20, marginBottom: 16 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                      <div><h2 style={{ fontWeight: 750 }}>{selected.title}</h2><small>{selected.description}</small></div>
                      <button className="ta-btn ta-btn-primary" disabled={running} onClick={() => void runWorkflow()}><Play size={15} /> {running ? "Đang chạy..." : "Chạy workflow"}</button>
                    </div>
                    <div style={{ display: "flex", alignItems: "stretch", gap: 8, marginTop: 18, overflowX: "auto" }}>
                      {selected.nodes.map((node, index) => (
                        <div key={node.id} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <div style={{ minWidth: 155, padding: 13, border: "1px solid var(--border)", borderRadius: 9, background: node.type === "HUMAN_APPROVAL" ? "#FEF3C7" : "#FAFBFC" }}>
                            <div style={{ display: "flex", gap: 7, alignItems: "center", fontWeight: 700 }}>{nodeIcon(node.type)} {node.type}</div>
                            <small>{node.name}</small>
                          </div>
                          {index < selected.nodes.length - 1 && <span>→</span>}
                        </div>
                      ))}
                    </div>
                  </div>
                  <div className="ta-card" style={{ padding: 20 }}>
                    <h3 style={{ display: "flex", alignItems: "center", gap: 7, fontWeight: 700, marginBottom: 12 }}><Clock size={17} /> Lịch sử chạy</h3>
                    {runs.length === 0 ? <p style={{ color: "var(--text-muted)" }}>Workflow chưa được chạy.</p> : runs.map((run) => (
                      <div key={run.id} style={{ display: "flex", justifyContent: "space-between", borderTop: "1px solid var(--border)", padding: "11px 0" }}>
                        <span>{new Date(run.created_at).toLocaleString("vi-VN")}</span>
                        <span className={`ta-badge ${run.status === "COMPLETED" ? "ta-badge-success" : "ta-badge-warning"}`}>{run.status}</span>
                      </div>
                    ))}
                  </div>
                </>
              ) : <div className="ta-card" style={{ padding: 30, textAlign: "center" }}>Chọn một workflow.</div>}
            </section>
          </div>
        </main>
      </div>

      {showCreate && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.45)", display: "grid", placeItems: "center", zIndex: 100 }}>
          <form className="ta-card" onSubmit={createWorkflow} style={{ width: 430, padding: 24 }}>
            <h2 style={{ fontWeight: 750, marginBottom: 14 }}>Tạo workflow mẫu an toàn</h2>
            <label style={{ display: "block", marginBottom: 6 }}>Tên workflow</label>
            <input className="ta-input" value={title} onChange={(event) => setTitle(event.target.value)} required minLength={2} />
            <p style={{ color: "var(--text-muted)", fontSize: 12, marginTop: 10 }}>Có thể chỉnh node/config qua API; builder trực quan nâng cao sẽ dùng cùng định dạng DAG này.</p>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 18 }}>
              <button type="button" className="ta-btn ta-btn-ghost" onClick={() => setShowCreate(false)}>Hủy</button>
              <button className="ta-btn ta-btn-primary"><Plus size={15} /> Tạo</button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
