"use client";

import axios from "axios";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Database,
  FileText,
  Layers3,
  Loader2,
  LockKeyhole,
  RefreshCw,
  Save,
  Search,
  ShieldCheck,
  Wrench,
} from "lucide-react";

import Sidebar from "@/components/Sidebar";
import api from "@/lib/api";
import { useAuthStore } from "@/store/useAuthStore";

interface Agent {
  id: string;
  name: string;
  role_code: string;
  system_prompt: string;
  prompt_overlay: string | null;
  model_name: string;
  is_active: boolean;
  tools_access: string[];
  allowed_actions: string[];
  disallowed_actions: string[];
  knowledge_access: string[];
  avatar_emoji?: string | null;
  description?: string | null;
}

interface ToolOption {
  name: string;
  description: string;
}

interface ChunkOption {
  id: string;
  chunk_index: number;
  section_title: string;
  page_start: number | null;
  page_end: number | null;
  status: string;
  confidentiality: string;
}

interface DocumentOption {
  document_id: string;
  document_name: string;
  document_title: string;
  collection_name: string;
  department_access: string;
  confidentiality: string;
  status: string;
  chunks: ChunkOption[];
}

interface ConfigurationOptions {
  agent_role: string;
  tools: ToolOption[];
  documents: DocumentOption[];
  // Selectors the agent still holds for knowledge that has since been deleted.
  orphaned_knowledge?: string[];
  // Tools granted to the agent that its role never uses; dropped on the next save.
  unsupported_grants?: string[];
}

interface AgentDraft {
  name: string;
  description: string;
  prompt_overlay: string;
  model_name: string;
  is_active: boolean;
  tools: string[];
  knowledgeAccess: string[];
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

function normalizeKnowledgeAccess(values: string[]) {
  if (!values.length) return ["*"];
  return values.map((value) => {
    if (value === "*" || value === "none" || value.includes(":")) return value;
    return `collection:${value}`;
  });
}

function pageLabel(start: number | null, end: number | null) {
  if (start == null) return "Không rõ trang";
  return end != null && end !== start ? `Trang ${start}–${end}` : `Trang ${start}`;
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
  const [documentQuery, setDocumentQuery] = useState("");
  const [expandedCollections, setExpandedCollections] = useState<Set<string>>(new Set());
  const [expandedDocuments, setExpandedDocuments] = useState<Set<string>>(new Set());

  const canConfigure = ["Owner", "Admin", "CEO"].includes(user?.role || "");

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
        model_name: agent.model_name,
        is_active: agent.is_active,
        tools: (agent.tools_access || []).filter(
          (tool) => !(agent.disallowed_actions || []).includes(tool) && offered.has(tool)
        ),
        knowledgeAccess: normalizeKnowledgeAccess(agent.knowledge_access || []),
      });
      setDocumentQuery("");
      setExpandedCollections(new Set());
      setExpandedDocuments(new Set());
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
    const timer = window.setTimeout(() => void loadAgents(), 0);
    return () => window.clearTimeout(timer);
  }, [canConfigure, hasHydrated, isAuthenticated, loadAgents, router]);

  useEffect(() => {
    if (!canConfigure || !selectedRole) return;
    const timer = window.setTimeout(() => void loadEditor(selectedRole), 0);
    return () => window.clearTimeout(timer);
  }, [canConfigure, loadEditor, selectedRole]);

  const collections = useMemo(() => {
    const query = documentQuery.trim().toLocaleLowerCase("vi");
    const grouped = new Map<string, DocumentOption[]>();
    for (const document of options?.documents || []) {
      const searchable = [
        document.document_name,
        document.document_title,
        document.document_id,
        document.collection_name,
      ].join(" ").toLocaleLowerCase("vi");
      if (query && !searchable.includes(query)) continue;
      const items = grouped.get(document.collection_name) || [];
      items.push(document);
      grouped.set(document.collection_name, items);
    }
    return [...grouped.entries()].sort(([left], [right]) => left.localeCompare(right, "vi"));
  }, [documentQuery, options?.documents]);

  const knowledgeMode = draft?.knowledgeAccess.includes("*")
    ? "all"
    : draft?.knowledgeAccess.includes("none")
      ? "none"
      : "custom";

  const setKnowledgeMode = (mode: "all" | "none" | "custom") => {
    setDraft((current) => current ? {
      ...current,
      knowledgeAccess: mode === "all" ? ["*"] : mode === "none" ? ["none"] : [],
    } : current);
  };

  const toggleTool = (tool: string) => {
    setDraft((current) => current ? {
      ...current,
      tools: current.tools.includes(tool)
        ? current.tools.filter((item) => item !== tool)
        : [...current.tools, tool].sort(),
    } : current);
  };

  const toggleSelector = (selector: string, inherited = false) => {
    if (inherited) return;
    setDraft((current) => {
      if (!current) return current;
      const custom = current.knowledgeAccess.filter((item) => item !== "*" && item !== "none");
      return {
        ...current,
        knowledgeAccess: custom.includes(selector)
          ? custom.filter((item) => item !== selector)
          : [...custom, selector].sort(),
      };
    });
  };

  const toggleExpanded = (
    setter: React.Dispatch<React.SetStateAction<Set<string>>>,
    key: string
  ) => {
    setter((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const save = async () => {
    if (!draft || !selectedRole || saving) return;
    if (knowledgeMode === "custom" && draft.knowledgeAccess.length === 0) {
      setError("Hãy chọn ít nhất một collection, tài liệu hoặc chunk; hoặc chọn Không truy cập.");
      return;
    }
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      const { data } = await api.patch<Agent>(`/api/v1/agents/${selectedRole}`, {
        name: draft.name.trim(),
        description: draft.description.trim(),
        prompt_overlay: draft.prompt_overlay.trim(),
        model_name: draft.model_name.trim(),
        is_active: draft.is_active,
        tools_access: draft.tools,
        allowed_actions: draft.tools,
        disallowed_actions: [],
        knowledge_access: draft.knowledgeAccess,
      });
      setAgents((current) => current.map((item) => item.role_code === data.role_code ? data : item));
      setDraft((current) => current ? {
        ...current,
        tools: data.tools_access || [],
        knowledgeAccess: normalizeKnowledgeAccess(data.knowledge_access || []),
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

        <main style={{ padding: "24px 30px 34px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16, marginBottom: 20 }}>
            <div>
              <h1 style={{ display: "flex", alignItems: "center", gap: 9, fontSize: "1.5rem", fontWeight: 800 }}><Bot size={25} color="var(--primary)" /> Cấu hình AI Employees</h1>
              <p style={{ marginTop: 6, color: "var(--text-muted)", fontSize: 13 }}>Quản lý model, tool và phạm vi tài liệu mỗi Agent được phép sử dụng.</p>
            </div>
            <button className="ta-btn" onClick={() => void loadAgents()} disabled={loading || saving}><RefreshCw size={15} className={loading ? "animate-spin" : ""} /> Làm mới</button>
          </div>

          {message && <div className="ta-card" style={{ padding: 13, marginBottom: 14, color: "#047857", background: "#ECFDF5", display: "flex", gap: 8, alignItems: "center" }}><CheckCircle2 size={17} /> {message}</div>}
          {error && <div className="ta-card" style={{ padding: 13, marginBottom: 14, color: "#B91C1C", background: "#FEF2F2" }}>{error}</div>}

          <div style={{ display: "grid", gridTemplateColumns: "280px minmax(0, 1fr)", gap: 18, alignItems: "start" }}>
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
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                      <label style={{ display: "grid", gap: 5, fontSize: 11, fontWeight: 700 }}>Tên Agent<input className="ta-input" value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></label>
                      <label style={{ display: "grid", gap: 5, fontSize: 11, fontWeight: 700 }}>Model<input className="ta-input" value={draft.model_name} onChange={(event) => setDraft({ ...draft, model_name: event.target.value })} /></label>
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

                  <section className="ta-card" style={{ padding: 20 }}>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, marginBottom: 14 }}>
                      <div>
                        <h2 style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 16, fontWeight: 800 }}><Database size={18} color="var(--primary)" /> Phạm vi kho tri thức</h2>
                        <p style={{ marginTop: 3, color: "var(--text-muted)", fontSize: 11 }}>Giới hạn collection, tài liệu hoặc chunk Agent được phép truy xuất khi dùng RAG.</p>
                      </div>
                      <span className="ta-badge ta-badge-info">{knowledgeMode === "all" ? "Toàn bộ" : knowledgeMode === "none" ? "Không truy cập" : `${draft.knowledgeAccess.length} phạm vi`}</span>
                    </div>

                    <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8, marginBottom: 13 }}>
                      {([
                        ["all", "Toàn bộ tài liệu", "Agent có thể tìm trong mọi tài liệu hợp lệ."],
                        ["custom", "Chọn phạm vi", "Chọn theo collection, tài liệu hoặc chunk."],
                        ["none", "Không truy cập", "Chặn hoàn toàn truy xuất kho tri thức."],
                      ] as const).map(([mode, label, description]) => (
                        <button key={mode} type="button" onClick={() => setKnowledgeMode(mode)} style={{ padding: 11, border: knowledgeMode === mode ? "1px solid #818CF8" : "1px solid var(--border)", borderRadius: 10, background: knowledgeMode === mode ? "#EEF2FF" : "#fff", color: knowledgeMode === mode ? "#3730A3" : "var(--text-dark)", textAlign: "left", cursor: "pointer" }}><strong style={{ display: "block", fontSize: 12 }}>{label}</strong><span style={{ display: "block", marginTop: 3, color: "var(--text-muted)", fontSize: 10.5, lineHeight: 1.4 }}>{description}</span></button>
                      ))}
                    </div>

                    {knowledgeMode === "custom" && (
                      <div style={{ border: "1px solid var(--border)", borderRadius: 11, overflow: "hidden" }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 8, padding: 10, borderBottom: "1px solid var(--border)", background: "#F8FAFC" }}><Search size={15} color="var(--text-muted)" /><input value={documentQuery} onChange={(event) => setDocumentQuery(event.target.value)} placeholder="Tìm collection hoặc tài liệu..." style={{ flex: 1, border: 0, outline: 0, background: "transparent", fontSize: 12 }} /></div>
                        <div style={{ maxHeight: 520, overflowY: "auto" }}>
                          {(options.orphaned_knowledge || []).length > 0 && (
                            <div style={{ borderBottom: "1px solid var(--border)", background: "#FFFBEB", padding: "10px 12px" }}>
                              <strong style={{ display: "block", fontSize: 12, color: "#92400E" }}>Phạm vi trỏ tới tài liệu đã bị xoá</strong>
                              <span style={{ display: "block", marginTop: 2, color: "#92400E", fontSize: 10.5 }}>Không còn khớp tài liệu nào. Bỏ chọn để gỡ khỏi cấu hình.</span>
                              <div style={{ display: "grid", gap: 5, marginTop: 8 }}>
                                {(options.orphaned_knowledge || []).map((selector) => (
                                  <label key={selector} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11, cursor: "pointer" }}>
                                    <input type="checkbox" checked={draft.knowledgeAccess.includes(selector)} onChange={() => toggleSelector(selector)} />
                                    <FileText size={13} color="#B45309" />
                                    <span style={{ overflowWrap: "anywhere" }}>{selector.replace(/^(document|chunk|collection):/, "")}</span>
                                    <span style={{ color: "#B45309", fontSize: 10 }}>đã bị xoá</span>
                                  </label>
                                ))}
                              </div>
                            </div>
                          )}
                          {collections.length === 0 && <p style={{ padding: 20, color: "var(--text-muted)", fontSize: 12, textAlign: "center" }}>Không tìm thấy tài liệu.</p>}
                          {collections.map(([collectionName, documents]) => {
                            const collectionSelector = `collection:${collectionName}`;
                            const collectionSelected = draft.knowledgeAccess.includes(collectionSelector);
                            const collectionOpen = expandedCollections.has(collectionName) || Boolean(documentQuery.trim());
                            return (
                              <div key={collectionName} style={{ borderBottom: "1px solid var(--border)" }}>
                                <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "10px 12px", background: collectionSelected ? "#F0FDF4" : "#fff" }}>
                                  <button type="button" aria-label="Mở collection" onClick={() => toggleExpanded(setExpandedCollections, collectionName)} style={{ display: "grid", placeItems: "center", padding: 0, border: 0, background: "transparent", cursor: "pointer" }}>{collectionOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}</button>
                                  <input type="checkbox" checked={collectionSelected} onChange={() => toggleSelector(collectionSelector)} />
                                  <Layers3 size={15} color="#6366F1" />
                                  <strong style={{ flex: 1, fontSize: 12 }}>{collectionName}</strong>
                                  <span style={{ color: "var(--text-muted)", fontSize: 10.5 }}>{documents.length} tài liệu</span>
                                </div>
                                {collectionOpen && documents.map((document) => {
                                  const documentSelector = `document:${document.document_id}`;
                                  const documentDirect = draft.knowledgeAccess.includes(documentSelector);
                                  const documentInherited = collectionSelected;
                                  const documentOpen = expandedDocuments.has(document.document_id);
                                  return (
                                    <div key={document.document_id} style={{ borderTop: "1px solid #F1F5F9", background: "#FAFBFC" }}>
                                      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "9px 12px 9px 38px" }}>
                                        <button type="button" aria-label="Mở danh sách chunk" onClick={() => toggleExpanded(setExpandedDocuments, document.document_id)} style={{ display: "grid", placeItems: "center", padding: 0, border: 0, background: "transparent", cursor: "pointer" }}>{documentOpen ? <ChevronDown size={15} /> : <ChevronRight size={15} />}</button>
                                        <input type="checkbox" checked={documentDirect || documentInherited} disabled={documentInherited} onChange={() => toggleSelector(documentSelector, documentInherited)} />
                                        <FileText size={14} color="#64748B" />
                                        <span style={{ minWidth: 0, flex: 1 }}><strong style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 11.5 }}>{document.document_title}</strong><span style={{ display: "block", marginTop: 2, color: "var(--text-muted)", fontSize: 10 }}>{document.department_access} · {document.confidentiality} · {document.chunks.length} chunks</span></span>
                                        {documentInherited && <span title="Được cấp từ collection" style={{ color: "#047857" }}><LockKeyhole size={13} /></span>}
                                      </div>
                                      {documentOpen && <div style={{ padding: "0 12px 9px 76px", display: "grid", gap: 5 }}>{document.chunks.map((chunk) => {
                                        const chunkSelector = `chunk:${chunk.id}`;
                                        const inherited = documentInherited || documentDirect;
                                        const checked = inherited || draft.knowledgeAccess.includes(chunkSelector);
                                        return <label key={chunk.id} style={{ display: "flex", alignItems: "center", gap: 8, padding: "7px 8px", borderRadius: 7, background: checked ? "#F0FDF4" : "#fff", color: inherited ? "var(--text-muted)" : "var(--text-dark)", cursor: inherited ? "default" : "pointer" }}><input type="checkbox" checked={checked} disabled={inherited} onChange={() => toggleSelector(chunkSelector, inherited)} /><span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 10.5 }}>#{chunk.chunk_index} · {chunk.section_title}</span><span style={{ color: "var(--text-muted)", fontSize: 9.5 }}>{pageLabel(chunk.page_start, chunk.page_end)}</span></label>;
                                      })}</div>}
                                    </div>
                                  );
                                })}
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    )}
                  </section>

                  <div style={{ display: "flex", justifyContent: "flex-end", gap: 9, paddingBottom: 10 }}>
                    <button className="ta-btn" onClick={() => void loadEditor(selectedRole)} disabled={saving}><RefreshCw size={15} /> Hoàn tác</button>
                    <button className="ta-btn ta-btn-primary" onClick={() => void save()} disabled={saving || !draft.name.trim() || !draft.model_name.trim()}><Save size={15} /> {saving ? "Đang lưu..." : "Lưu cấu hình"}</button>
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
