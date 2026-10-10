"use client";

import axios from "axios";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Bot,
  CheckCircle2,
  BookOpen,
  Database,
  Loader2,
  RefreshCw,
  Save,
  ShieldCheck,
  Wrench,
} from "lucide-react";

import Sidebar from "@/components/Sidebar";
import KnowledgeScopePicker, { type DocumentOption, tickedSelectors } from "@/components/admin/KnowledgeScopePicker";
import api from "@/lib/api";
import { useAuthStore, userCan } from "@/store/useAuthStore";

interface Agent {
  id: string;
  name: string;
  role_code: string;
  system_prompt: string;
  prompt_overlay: string | null;
  // null runs the AI service's default model.
  model_name: string | null;
  is_active: boolean;
  tools_access: string[];
  allowed_actions: string[];
  disallowed_actions: string[];
  knowledge_access: string[];
  // The skill shelf, for roles that read one (supports_skills).
  skill_access?: string[];
  supports_skills?: boolean;
  avatar_emoji?: string | null;
  description?: string | null;
}

interface ToolOption {
  name: string;
  description: string;
}

interface ConfigurationOptions {
  agent_role: string;
  tools: ToolOption[];
  documents: DocumentOption[];
  // Selectors the agent still holds for knowledge that has since been deleted.
  orphaned_knowledge?: string[];
  orphaned_skills?: string[];
  // Tools granted to the agent that its role never uses; dropped on the next save.
  unsupported_grants?: string[];
}

interface ModelOption {
  id: string;
  provider: string;
  label: string;
  // Usage of a model without a pricing row is not counted on the cost page.
  priced: boolean;
}

interface ModelOptions {
  default: { provider: string; id: string } | null;
  models: ModelOption[];
  // Vendors whose model listing failed; only their configured default is offered.
  errors: Record<string, string>;
}

const PROVIDER_LABELS: Record<string, string> = {
  gemini: "Google Gemini",
  openai: "OpenAI",
  bedrock: "Amazon Bedrock",
};

interface AgentDraft {
  name: string;
  description: string;
  prompt_overlay: string;
  model_name: string;
  is_active: boolean;
  tools: string[];
  knowledgeAccess: string[];
  skillAccess: string[];
  supportsSkills: boolean;
}

const ROLE_LABELS: Record<string, string> = {
  CEO: "Điều hành",
  HR: "Nhân sự",
  LEGAL: "Pháp chế",
  IT: "Công nghệ thông tin",
  FINANCE: "Tài chính",
  SALES: "Kinh doanh",
  KNOWLEDGE: "Kho tri thức",
};

function messageFrom(error: unknown) {
  if (!axios.isAxiosError(error)) return "Không thể xử lý yêu cầu.";
  const detail = (error.response?.data as { detail?: unknown } | undefined)?.detail;
  return typeof detail === "string" ? detail : error.message;
}

export default function AIEditorPage() {
  const router = useRouter();
  const { isAuthenticated, hasHydrated, user } = useAuthStore();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [selectedRole, setSelectedRole] = useState("");
  const [options, setOptions] = useState<ConfigurationOptions | null>(null);
  const [draft, setDraft] = useState<AgentDraft | null>(null);
  const [loading, setLoading] = useState(true);
  const [editorLoading, setEditorLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [modelOptions, setModelOptions] = useState<ModelOptions | null>(null);
  const [modelLoading, setModelLoading] = useState(false);
  const [modelError, setModelError] = useState<string | null>(null);

  // "Cấu hình nhân viên AI" in org-structure; the server checks the same code.
  const canConfigure = userCan(user, "agents.configure");

  const loadAgents = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const { data } = await api.get<Agent[]>("/api/v1/agents/");
      setAgents(data);
      setSelectedRole((current) =>
        current && data.some((item) => item.role_code === current)
          ? current
          : data[0]?.role_code || ""
      );
    } catch (reason) {
      setError(messageFrom(reason));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadModels = useCallback(async (refresh = false) => {
    setModelLoading(true);
    setModelError(null);
    try {
      const { data } = await api.get<ModelOptions>("/api/v1/agents/model-options", {
        params: refresh ? { refresh: true } : undefined,
      });
      setModelOptions(data);
    } catch (reason) {
      setModelError(messageFrom(reason));
    } finally {
      setModelLoading(false);
    }
  }, []);

  const modelGroups = useMemo(() => {
    const grouped = new Map<string, ModelOption[]>();
    for (const model of modelOptions?.models || []) {
      const items = grouped.get(model.provider) || [];
      items.push(model);
      grouped.set(model.provider, items);
    }
    return [...grouped.entries()];
  }, [modelOptions]);

  const loadEditor = useCallback(async (role: string) => {
    if (!role) return;
    setEditorLoading(true);
    setError(null);
    setMessage(null);
    try {
      const [agentResponse, optionResponse] = await Promise.all([
        api.get<Agent>(`/api/v1/agents/${role}`),
        api.get<ConfigurationOptions>(`/api/v1/agents/${role}/configuration-options`),
      ]);
      const agent = agentResponse.data;
      const offered = new Set(optionResponse.data.tools.map((tool) => tool.name));
      setOptions(optionResponse.data);
      setDraft({
        name: agent.name,
        description: agent.description || "",
        prompt_overlay: agent.prompt_overlay ?? "",
        model_name: agent.model_name ?? "",
        is_active: agent.is_active,
        tools: (agent.tools_access || []).filter(
          (tool) => !(agent.disallowed_actions || []).includes(tool) && offered.has(tool)
        ),
        knowledgeAccess: tickedSelectors(agent.knowledge_access),
        skillAccess: tickedSelectors(agent.skill_access),
        supportsSkills: Boolean(agent.supports_skills),
      });
    } catch (reason) {
      setError(messageFrom(reason));
      setOptions(null);
      setDraft(null);
    } finally {
      setEditorLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!hasHydrated) return;
    if (!isAuthenticated) {
      router.replace("/login");
      return;
    }
    if (!canConfigure) {
      router.replace("/dashboard");
      return;
    }
    const timer = window.setTimeout(() => {
      void loadAgents();
      void loadModels();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [canConfigure, hasHydrated, isAuthenticated, loadAgents, loadModels, router]);

  useEffect(() => {
    if (!canConfigure || !selectedRole) return;
    const timer = window.setTimeout(() => void loadEditor(selectedRole), 0);
    return () => window.clearTimeout(timer);
  }, [canConfigure, loadEditor, selectedRole]);

  const toggleTool = (tool: string) => {
    setDraft((current) => current ? {
      ...current,
      tools: current.tools.includes(tool)
        ? current.tools.filter((item) => item !== tool)
        : [...current.tools, tool].sort(),
    } : current);
  };

  const save = async () => {
    if (!draft || !selectedRole || saving) return;
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      const { data } = await api.patch<Agent>(`/api/v1/agents/${selectedRole}`, {
        name: draft.name.trim(),
        description: draft.description.trim(),
        prompt_overlay: draft.prompt_overlay.trim(),
        // Blank returns the agent to the AI service's default model.
        model_name: draft.model_name,
        is_active: draft.is_active,
        tools_access: draft.tools,
        allowed_actions: draft.tools,
        disallowed_actions: [],
        knowledge_access: draft.knowledgeAccess,
        ...(draft.supportsSkills ? { skill_access: draft.skillAccess } : {}),
      });
      setAgents((current) => current.map((item) => item.role_code === data.role_code ? data : item));
      setDraft((current) => current ? {
        ...current,
        tools: data.tools_access || [],
        knowledgeAccess: tickedSelectors(data.knowledge_access),
        skillAccess: tickedSelectors(data.skill_access),
      } : current);
      setMessage(`Đã lưu quyền cho ${data.name}.`);
    } catch (reason) {
      setError(messageFrom(reason));
    } finally {
      setSaving(false);
    }
  };

  if (!hasHydrated || !isAuthenticated || !canConfigure) return null;

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "var(--body-bg)" }}>
      <Sidebar />
      <div style={{ flex: 1, minWidth: 0 }}>
        <header className="ta-topbar">
          <div className="breadcrumb"><span>Home</span><span className="breadcrumb-sep">›</span><span className="breadcrumb-current">Cấu hình AI Employees</span></div>
          <span className="ta-badge ta-badge-info" style={{ display: "inline-flex", gap: 5, alignItems: "center" }}><ShieldCheck size={12} /> Owner / Admin / CEO</span>
        </header>

        <main className="page-main" style={{ padding: "24px 30px 34px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16, marginBottom: 20 }}>
            <div>
              <h1 style={{ display: "flex", alignItems: "center", gap: 9, fontSize: "1.5rem", fontWeight: 800 }}><Bot size={25} color="var(--primary)" /> Cấu hình AI Employees</h1>
              <p style={{ marginTop: 6, color: "var(--text-muted)", fontSize: 13 }}>Quản lý model, tool và phạm vi tài liệu mỗi Agent được phép sử dụng.</p>
            </div>
            <button className="ta-btn" onClick={() => void loadAgents()} disabled={loading || saving}><RefreshCw size={15} className={loading ? "animate-spin" : ""} /> Làm mới</button>
          </div>

          {message && <div className="ta-card" style={{ padding: 13, marginBottom: 14, color: "#047857", background: "#ECFDF5", display: "flex", gap: 8, alignItems: "center" }}><CheckCircle2 size={17} /> {message}</div>}
          {error && <div className="ta-card" style={{ padding: 13, marginBottom: 14, color: "#B91C1C", background: "#FEF2F2" }}>{error}</div>}

          <div className="stack-mobile" style={{ display: "grid", gridTemplateColumns: "280px minmax(0, 1fr)", gap: 18, alignItems: "start" }}>
            <aside className="ta-card" style={{ padding: 10, position: "sticky", top: 16 }}>
              <div style={{ padding: "8px 9px 11px", borderBottom: "1px solid var(--border)" }}>
                <strong style={{ fontSize: 13 }}>Danh sách Agent</strong>
                <span style={{ display: "block", marginTop: 3, color: "var(--text-muted)", fontSize: 11 }}>{agents.length} AI Employees trong workspace</span>
              </div>
              <div style={{ display: "grid", gap: 5, paddingTop: 8 }}>
                {loading ? <div style={{ padding: 20, textAlign: "center" }}><Loader2 className="animate-spin" size={20} color="var(--primary)" /></div> : agents.map((agent) => {
                  const active = selectedRole === agent.role_code;
                  return (
                    <button
                      key={agent.id}
                      type="button"
                      onClick={() => setSelectedRole(agent.role_code)}
                      style={{ display: "flex", alignItems: "center", gap: 10, width: "100%", padding: "10px 11px", border: active ? "1px solid #C7D2FE" : "1px solid transparent", borderRadius: 10, background: active ? "#EEF2FF" : "transparent", color: active ? "#3730A3" : "var(--text-dark)", cursor: "pointer", textAlign: "left" }}
                    >
                      <span style={{ width: 36, height: 36, display: "grid", placeItems: "center", flex: "0 0 auto", borderRadius: 10, background: active ? "#fff" : "#F8FAFC", fontSize: 19 }}>{agent.avatar_emoji || "🤖"}</span>
                      <span style={{ minWidth: 0, flex: 1 }}>
                        <strong style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 12.5 }}>{agent.name}</strong>
                        <span style={{ display: "block", marginTop: 2, color: "var(--text-muted)", fontSize: 10.5 }}>{agent.role_code} · {ROLE_LABELS[agent.role_code] || "AI Agent"}</span>
                      </span>
                      <span title={agent.is_active ? "Đang hoạt động" : "Đã tắt"} style={{ width: 8, height: 8, borderRadius: "50%", background: agent.is_active ? "#10B981" : "#CBD5E1" }} />
                    </button>
                  );
                })}
              </div>
            </aside>

            <section style={{ minWidth: 0, display: "grid", gap: 16 }}>
              {editorLoading || !draft || !options ? (
                <div className="ta-card" style={{ minHeight: 360, display: "grid", placeItems: "center" }}><div style={{ textAlign: "center", color: "var(--text-muted)" }}><Loader2 className="animate-spin" size={26} color="var(--primary)" /><p style={{ marginTop: 9 }}>Đang tải cấu hình Agent...</p></div></div>
              ) : (
                <>
                  <section className="ta-card" style={{ padding: 20 }}>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 14, marginBottom: 16 }}>
                      <div>
                        <h2 style={{ fontSize: 16, fontWeight: 800 }}>Thông tin Agent</h2>
                        <p style={{ marginTop: 3, color: "var(--text-muted)", fontSize: 11 }}>Tên, model và chỉ dẫn hệ thống áp dụng cho Agent đã chọn.</p>
                      </div>
                      <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, fontWeight: 700, cursor: "pointer" }}>
                        <input type="checkbox" checked={draft.is_active} onChange={(event) => setDraft({ ...draft, is_active: event.target.checked })} />
                        {draft.is_active ? "Đang hoạt động" : "Đã tắt"}
                      </label>
                    </div>
                    <div className="stack-mobile" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                      <label style={{ display: "grid", gap: 5, fontSize: 11, fontWeight: 700 }}>Tên Agent<input className="ta-input" value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></label>
                      <label style={{ display: "grid", gap: 5, fontSize: 11, fontWeight: 700 }}>
                        <span style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                          Model
                          <button type="button" onClick={() => void loadModels(true)} disabled={modelLoading} title="Lấy lại danh sách từ nhà cung cấp" style={{ display: "inline-flex", alignItems: "center", gap: 4, padding: 0, border: 0, background: "transparent", color: "var(--primary)", fontSize: 10, fontWeight: 600, cursor: "pointer" }}>
                            <RefreshCw size={11} className={modelLoading ? "animate-spin" : ""} /> Làm mới
                          </button>
                        </span>
                        <select
                          className="ta-input"
                          value={draft.model_name}
                          disabled={modelLoading && !modelOptions}
                          onChange={(event) => setDraft({ ...draft, model_name: event.target.value })}
                        >
                          <option value="">
                            Mặc định hệ thống{modelOptions?.default ? ` (${modelOptions.default.id})` : ""}
                          </option>
                          {modelGroups.map(([provider, models]) => (
                            <optgroup key={provider} label={PROVIDER_LABELS[provider] || provider}>
                              {models.map((model) => (
                                <option key={model.id} value={model.id}>
                                  {model.label !== model.id ? `${model.label} — ${model.id}` : model.id}
                                  {model.priced ? "" : " · chưa có bảng giá"}
                                </option>
                              ))}
                            </optgroup>
                          ))}
                          {draft.model_name && !modelOptions?.models.some((model) => model.id === draft.model_name) && (
                            <option value={draft.model_name}>{draft.model_name} (không có trong danh sách hiện tại)</option>
                          )}
                        </select>
                        <span style={{ fontWeight: 400, fontSize: 10, lineHeight: 1.5, opacity: 0.75 }}>
                          {modelLoading && !modelOptions
                            ? "Đang lấy danh sách model từ nhà cung cấp…"
                            : modelError
                              ? modelError
                              : Object.keys(modelOptions?.errors || {}).length
                                ? `Không đọc được danh sách của ${Object.keys(modelOptions?.errors || {}).map((key) => PROVIDER_LABELS[key] || key).join(", ")}; chỉ hiện model mặc định của nhà cung cấp đó.`
                                : "Lấy trực tiếp từ API của nhà cung cấp theo khoá đã cấu hình. Model chưa có bảng giá sẽ không được tính vào trang Chi phí."}
                        </span>
                      </label>
                    </div>
                    <label style={{ display: "grid", gap: 5, marginTop: 12, fontSize: 11, fontWeight: 700 }}>Mô tả<input className="ta-input" value={draft.description} onChange={(event) => setDraft({ ...draft, description: event.target.value })} /></label>
                    <label style={{ display: "grid", gap: 5, marginTop: 12, fontSize: 11, fontWeight: 700 }}>Quy ước riêng của công ty<textarea className="ta-input" value={draft.prompt_overlay} onChange={(event) => setDraft({ ...draft, prompt_overlay: event.target.value })} rows={6} placeholder="Để trống thì agent chạy đúng prompt gốc." style={{ resize: "vertical", lineHeight: 1.55 }} /><span style={{ fontWeight: 400, fontSize: 10, lineHeight: 1.5, opacity: 0.75 }}>Nối thêm vào cuối prompt trả lời mặc định, chỉ đổi cách diễn đạt. Muốn can thiệp định tuyến ý định thì dùng trang Plugin.</span></label>
                  </section>

                  <section className="ta-card" style={{ padding: 20 }}>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, marginBottom: 14 }}>
                      <div>
                        <h2 style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 16, fontWeight: 800 }}><Wrench size={18} color="var(--primary)" /> Quyền sử dụng tool</h2>
                        <p style={{ marginTop: 3, color: "var(--text-muted)", fontSize: 11 }}>Agent chỉ có thể thực thi những tool được bật tại đây.</p>
                      </div>
                      <span className="ta-badge ta-badge-info">{draft.tools.length}/{options.tools.length} tool</span>
                    </div>
                    {(options.unsupported_grants || []).length > 0 && <p style={{ marginBottom: 10, padding: "8px 10px", borderRadius: 8, background: "#FFFBEB", color: "#92400E", fontSize: 11 }}>Agent này đang được cấp {(options.unsupported_grants || []).join(", ")} — tool của vai trò khác, không có tác dụng với agent này. Sẽ được gỡ khi lưu cấu hình.</p>}
                    {options.tools.length === 0 ? <p style={{ color: "var(--text-muted)", fontSize: 12 }}>Agent này chưa có tool khả dụng.</p> : (
                      <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 9 }}>
                        {options.tools.map((tool) => {
                          const checked = draft.tools.includes(tool.name);
                          return (
                            <label key={tool.name} style={{ display: "flex", alignItems: "flex-start", gap: 10, padding: 12, border: checked ? "1px solid #A5B4FC" : "1px solid var(--border)", borderRadius: 10, background: checked ? "#EEF2FF" : "#fff", cursor: "pointer" }}>
                              <input type="checkbox" checked={checked} onChange={() => toggleTool(tool.name)} style={{ marginTop: 2 }} />
                              <span style={{ minWidth: 0 }}><strong style={{ display: "block", fontSize: 12, overflowWrap: "anywhere" }}>{tool.name}</strong><span style={{ display: "block", marginTop: 3, color: "var(--text-muted)", fontSize: 10.5, lineHeight: 1.45 }}>{tool.description}</span></span>
                            </label>
                          );
                        })}
                      </div>
                    )}
                  </section>

                  <KnowledgeScopePicker
                    key={`knowledge-${selectedRole}`}
                    icon={<Database size={18} color="var(--primary)" />}
                    title="Phạm vi kho tri thức"
                    description="Agent chỉ tìm trong collection, tài liệu hoặc chunk được tích. Tích cả collection thì tài liệu thêm vào collection đó sau này cũng được đọc."
                    emptyWarning="Chưa tích gì: agent sẽ không tra cứu được tài liệu nào trong kho tri thức."
                    documents={options.documents}
                    selected={draft.knowledgeAccess}
                    orphaned={options.orphaned_knowledge || []}
                    onChange={(next) => setDraft((current) => current ? { ...current, knowledgeAccess: next } : current)}
                  />

                  {draft.supportsSkills && (
                    <KnowledgeScopePicker
                      key={`skills-${selectedRole}`}
                      icon={<BookOpen size={18} color="#7C3AED" />}
                      title="Kho kỹ năng & hiểu biết"
                      description="Tài liệu dạy agent cách làm nghề: khung nội dung, giọng thương hiệu, kinh nghiệm viết. Agent dùng làm hướng dẫn khi viết, không trích dẫn như nguồn số liệu và không dùng để kiểm chứng."
                      emptyWarning="Chưa tích gì: agent viết theo hướng dẫn mặc định, không có kỹ năng riêng của công ty."
                      documents={options.documents}
                      selected={draft.skillAccess}
                      orphaned={options.orphaned_skills || []}
                      onChange={(next) => setDraft((current) => current ? { ...current, skillAccess: next } : current)}
                    />
                  )}

                  <div style={{ display: "flex", justifyContent: "flex-end", gap: 9, paddingBottom: 10 }}>
                    <button className="ta-btn" onClick={() => void loadEditor(selectedRole)} disabled={saving}><RefreshCw size={15} /> Hoàn tác</button>
                    <button className="ta-btn ta-btn-primary" onClick={() => void save()} disabled={saving || !draft.name.trim()}><Save size={15} /> {saving ? "Đang lưu..." : "Lưu cấu hình"}</button>
                  </div>
                </>
              )}
            </section>
          </div>
        </main>
      </div>
    </div>
  );
}
