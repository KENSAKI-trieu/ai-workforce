"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import axios from "axios";
import {
  Bot,
  Building2,
  Download,
  KeyRound,
  RefreshCw,
  Save,
  Shield,
  Trash2,
  Users,
} from "lucide-react";

import AdminShell from "@/components/admin/AdminShell";
import api from "@/lib/api";
import { useAuthStore, userCan } from "@/store/useAuthStore";

interface LLMKeyStatus {
  provider: string;
  label: string;
  // The company's own key, shown by its last characters only; null when none was entered.
  company_key: { hint: string; readable: boolean; updated_at: string | null } | null;
  // Whether the deployment's own key (.env) exists for this vendor.
  system_key: boolean;
}

interface AgentRow {
  role_code: string;
  name: string;
  // null runs the AI service's default model.
  model_name: string | null;
  is_active: boolean;
}

interface ModelOption {
  id: string;
  provider: string;
  label: string;
  priced: boolean;
}

interface ModelOptions {
  default: { provider: string; id: string } | null;
  models: ModelOption[];
  errors: Record<string, string>;
}

const PROVIDER_LABELS: Record<string, string> = {
  gemini: "Google Gemini",
  openai: "OpenAI",
  bedrock: "Amazon Bedrock",
};

function messageFrom(error: unknown) {
  if (!axios.isAxiosError(error)) return "Không thể xử lý yêu cầu.";
  const detail = (error.response?.data as { detail?: unknown } | undefined)?.detail;
  return typeof detail === "string" ? detail : error.message;
}

interface Workspace {
  id: string;
  name: string;
  domain: string;
  logo_url: string | null;
  timezone: string;
  language: string;
  data_retention_days: number;
  default_model: string;
  billing_email: string | null;
  notification_settings: Record<string, boolean>;
  security_settings: {
    mfa_required?: boolean;
    session_timeout_minutes?: number;
    allowed_email_domains?: string[];
    ip_allowlist?: string[];
  };
  supported_models: string[];
  llm_keys: LLMKeyStatus[];
  current_user_role: string;
}

interface Department {
  id: string;
  code: string;
  name: string;
  member_count: number;
  is_active: boolean;
}

export default function SettingsPage() {
  const { user } = useAuthStore();
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [message, setMessage] = useState("");
  const [deletionReason, setDeletionReason] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const canEdit = ["CEO", "Owner", "Admin"].includes(user?.role || "");
  // Xoá workspace là việc của người tạo công ty. "Owner" giữ lại cho tài khoản tạo
  // trước khi chuỗi role được đổi tên thành "CEO".
  const canRequestDeletion = ["CEO", "Owner"].includes(user?.role || "");
  const canConfigureAgents = userCan(user, "agents.configure");
  const [keyDrafts, setKeyDrafts] = useState<Record<string, string>>({});
  const [keyBusy, setKeyBusy] = useState<string | null>(null);
  const [keyError, setKeyError] = useState("");
  const [agents, setAgents] = useState<AgentRow[]>([]);
  const [modelOptions, setModelOptions] = useState<ModelOptions | null>(null);
  const [modelLoading, setModelLoading] = useState(false);
  const [modelError, setModelError] = useState("");
  const [savingAgent, setSavingAgent] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [workspaceResponse, departmentResponse] = await Promise.all([
      api.get("/api/v1/workspace"),
      api.get("/api/v1/workspace/departments"),
    ]);
    setWorkspace(workspaceResponse.data);
    setDepartments(departmentResponse.data);
  }, []);

  const loadModels = useCallback(async (refresh = false) => {
    if (!canConfigureAgents) return;
    setModelLoading(true);
    setModelError("");
    try {
      const [agentResponse, optionResponse] = await Promise.all([
        api.get<AgentRow[]>("/api/v1/agents"),
        api.get<ModelOptions>("/api/v1/agents/model-options", { params: refresh ? { refresh: true } : undefined }),
      ]);
      setAgents(agentResponse.data);
      setModelOptions(optionResponse.data);
    } catch (reason) {
      setModelError(messageFrom(reason));
    } finally {
      setModelLoading(false);
    }
  }, [canConfigureAgents]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void load();
      void loadModels();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [load, loadModels]);

  const modelGroups = useMemo(() => {
    const grouped = new Map<string, ModelOption[]>();
    for (const model of modelOptions?.models || []) {
      const items = grouped.get(model.provider) || [];
      items.push(model);
      grouped.set(model.provider, items);
    }
    return [...grouped.entries()];
  }, [modelOptions]);

  async function saveKey(provider: string) {
    const apiKey = (keyDrafts[provider] || "").trim();
    if (!apiKey) return;
    setKeyBusy(provider);
    setKeyError("");
    try {
      const { data } = await api.put<{ hint: string; models: number }>(`/api/v1/workspace/llm-keys/${provider}`, { api_key: apiKey });
      setKeyDrafts((current) => ({ ...current, [provider]: "" }));
      setMessage(`Đã kiểm tra và lưu key ${PROVIDER_LABELS[provider] || provider} (${data.hint}); key này mở được ${data.models} model.`);
      await Promise.all([load(), loadModels(true)]);
    } catch (reason) {
      setKeyError(messageFrom(reason));
    } finally {
      setKeyBusy(null);
    }
  }

  async function removeKey(provider: string) {
    const label = PROVIDER_LABELS[provider] || provider;
    if (!window.confirm(`Xoá key ${label} của công ty? Các agent sẽ quay về key hệ thống (nếu có).`)) return;
    setKeyBusy(provider);
    setKeyError("");
    try {
      await api.delete(`/api/v1/workspace/llm-keys/${provider}`);
      setMessage(`Đã xoá key ${label} của công ty.`);
      await Promise.all([load(), loadModels(true)]);
    } catch (reason) {
      setKeyError(messageFrom(reason));
    } finally {
      setKeyBusy(null);
    }
  }

  async function saveAgentModel(roleCode: string, modelName: string) {
    setSavingAgent(roleCode);
    setModelError("");
    try {
      const { data } = await api.patch<AgentRow>(`/api/v1/agents/${roleCode}`, { model_name: modelName });
      setAgents((current) => current.map((agent) => (agent.role_code === roleCode ? { ...agent, model_name: data.model_name } : agent)));
      setMessage(`Đã đổi model của agent ${data.name || roleCode} sang ${data.model_name || "mặc định hệ thống"}.`);
    } catch (reason) {
      setModelError(messageFrom(reason));
    } finally {
      setSavingAgent(null);
    }
  }

  function update<K extends keyof Workspace>(key: K, value: Workspace[K]) {
    if (workspace) setWorkspace({ ...workspace, [key]: value });
  }

  async function save() {
    if (!workspace) return;
    await api.patch("/api/v1/workspace", {
      name: workspace.name,
      logo_url: workspace.logo_url || null,
      timezone: workspace.timezone,
      language: workspace.language,
      data_retention_days: workspace.data_retention_days,
      default_model: workspace.default_model,
      billing_email: workspace.billing_email || null,
      notification_settings: workspace.notification_settings,
      security_settings: {
        mfa_required: workspace.security_settings.mfa_required || false,
        session_timeout_minutes: workspace.security_settings.session_timeout_minutes || 480,
        allowed_email_domains: workspace.security_settings.allowed_email_domains || [],
        ip_allowlist: workspace.security_settings.ip_allowlist || [],
      },
    });
    setMessage("Đã lưu cài đặt công ty và ghi Audit Log.");
    await load();
  }

  async function exportData() {
    const response = await api.get("/api/v1/workspace/export");
    const blob = new Blob([JSON.stringify(response.data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${workspace?.domain || "workspace"}-export.json`;
    link.click();
    URL.revokeObjectURL(url);
    setMessage("Đã export snapshot không chứa password hoặc credential reference.");
  }

  async function requestDeletion() {
    await api.post("/api/v1/workspace/data-deletion-request", {
      confirmation_domain: confirmation,
      reason: deletionReason,
    });
    setMessage("Yêu cầu xóa đã được ghi nhận để review; chưa có dữ liệu nào bị xóa.");
    setConfirmation("");
    setDeletionReason("");
  }

  if (!workspace) {
    return <AdminShell title="Cài đặt công ty" description="Đang tải cấu hình workspace…"><div className="ops-card ops-empty">Đang tải…</div></AdminShell>;
  }

  return (
    <AdminShell
      title="Cài đặt công ty"
      description="Quản lý nhận diện, chính sách dữ liệu, model, bảo mật, billing và vòng đời dữ liệu."
      action={<button className="ops-button" onClick={() => void save()} disabled={!canEdit}><Save size={15}/>Lưu thay đổi</button>}
    >
      {message && <div className="ops-alert">{message}</div>}
      {!canEdit && <div className="ops-alert error">Bạn có quyền xem nhưng không có quyền thay đổi cài đặt công ty.</div>}

      <div className="ops-grid ops-grid-2">
        <section className="ops-card">
          <div className="ops-card-header"><h2>Thông tin công ty</h2><Building2 size={19} color="#3c50e0"/></div>
          <div className="ops-form-grid">
            <div className="ops-field"><label>Tên công ty</label><input disabled={!canEdit} value={workspace.name} onChange={(e) => update("name", e.target.value)}/></div>
            <div className="ops-field"><label>Domain workspace</label><input disabled value={workspace.domain}/></div>
            <div className="ops-field full"><label>Logo URL (HTTPS)</label><input disabled={!canEdit} value={workspace.logo_url || ""} onChange={(e) => update("logo_url", e.target.value || null)} placeholder="https://company.example/logo.png"/></div>
            <div className="ops-field"><label>Múi giờ IANA</label><input disabled={!canEdit} value={workspace.timezone} onChange={(e) => update("timezone", e.target.value)}/></div>
            <div className="ops-field"><label>Ngôn ngữ</label><select disabled={!canEdit} value={workspace.language} onChange={(e) => update("language", e.target.value)}><option value="vi">Tiếng Việt</option><option value="ja">Tiếng Nhật</option><option value="en">English</option></select></div>
            <div className="ops-field"><label>Billing email</label><input disabled={!canEdit} type="email" value={workspace.billing_email || ""} onChange={(e) => update("billing_email", e.target.value || null)}/></div>
            <div className="ops-field"><label>Model mặc định</label><select disabled={!canEdit} value={workspace.default_model} onChange={(e) => update("default_model", e.target.value)}>{workspace.supported_models.map((model) => <option key={model}>{model}</option>)}</select></div>
          </div>
        </section>

        <section className="ops-card">
          <div className="ops-card-header"><h2>Data & Security</h2><Shield size={19} color="#3c50e0"/></div>
          <div className="ops-form-grid">
            <div className="ops-field"><label>Lưu dữ liệu (ngày)</label><input disabled={!canEdit} type="number" min={30} max={3650} value={workspace.data_retention_days} onChange={(e) => update("data_retention_days", Number(e.target.value))}/></div>
            <div className="ops-field"><label>Session timeout (phút)</label><input disabled={!canEdit} type="number" min={15} max={10080} value={workspace.security_settings.session_timeout_minutes || 480} onChange={(e) => update("security_settings", {...workspace.security_settings, session_timeout_minutes: Number(e.target.value)})}/></div>
            <div className="ops-field full"><label>Email domains cho phép</label><input disabled={!canEdit} value={(workspace.security_settings.allowed_email_domains || []).join(", ")} onChange={(e) => update("security_settings", {...workspace.security_settings, allowed_email_domains: e.target.value.split(",").map((item) => item.trim()).filter(Boolean)})} placeholder="company.com, subsidiary.vn"/></div>
            <div className="ops-field full"><label>IP/CIDR allowlist</label><input disabled={!canEdit} value={(workspace.security_settings.ip_allowlist || []).join(", ")} onChange={(e) => update("security_settings", {...workspace.security_settings, ip_allowlist: e.target.value.split(",").map((item) => item.trim()).filter(Boolean)})} placeholder="10.0.0.0/8, 203.0.113.10/32"/></div>
          </div>
          <label style={{ display: "flex", gap: 9, marginTop: 14, fontSize: ".8rem" }}>
            <input disabled={!canEdit} type="checkbox" checked={workspace.security_settings.mfa_required || false} onChange={(e) => update("security_settings", {...workspace.security_settings, mfa_required: e.target.checked})}/>
            Yêu cầu MFA theo chính sách công ty
          </label>
        </section>
      </div>

      <div className="ops-grid ops-grid-2 ops-section">
        <section className="ops-card">
          <div className="ops-card-header"><h2>Phòng ban</h2><Users size={19} color="#64748b"/></div>
          {departments.map((department) => (
            <div key={department.id} style={{ display: "flex", justifyContent: "space-between", padding: "10px 0", borderTop: "1px solid #eef2f7" }}>
              <span><strong>{department.name}</strong><br/><small className="ops-muted">{department.code}</small></span>
              <span className={`ops-badge ${department.is_active ? "success" : "error"}`}>{department.member_count} thành viên</span>
            </div>
          ))}
          <a className="ops-button secondary" href="/users-mgmt" style={{ marginTop: 12, textDecoration: "none" }}>Quản lý thành viên & vai trò</a>
        </section>

        <section className="ops-card">
          <div className="ops-card-header"><h2>API keys của công ty</h2><KeyRound size={19} color="#64748b"/></div>
          <p className="ops-muted" style={{ fontSize: ".75rem", margin: "0 0 6px" }}>
            Key nhập ở đây thay key hệ thống của đúng nhà cung cấp đó cho mọi agent của công ty. Key được kiểm tra với nhà cung cấp trước khi lưu, lưu mã hoá và không bao giờ hiển thị lại, chỉ hiện 4 ký tự cuối.
          </p>
          {keyError && <div className="ops-alert error">{keyError}</div>}
          {workspace.llm_keys.map((item) => (
            <div key={item.provider} style={{ padding: "12px 0", borderTop: "1px solid #eef2f7" }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
                <strong>{item.label}</strong>
                {item.company_key ? (
                  <span className={`ops-badge ${item.company_key.readable ? "success" : "error"}`}>
                    {item.company_key.readable ? `Key công ty ${item.company_key.hint}` : `Key công ty ${item.company_key.hint} không giải mã được, hãy nhập lại`}
                  </span>
                ) : (
                  <span className={`ops-badge ${item.system_key ? "warning" : "error"}`}>{item.system_key ? "Đang dùng key hệ thống" : "Chưa có key"}</span>
                )}
              </div>
              {canEdit && (
                <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
                  <input
                    type="password"
                    autoComplete="off"
                    spellCheck={false}
                    value={keyDrafts[item.provider] || ""}
                    onChange={(e) => setKeyDrafts((current) => ({ ...current, [item.provider]: e.target.value }))}
                    placeholder={item.company_key ? "Nhập key mới để thay" : item.provider === "openai" ? "sk-..." : "AIza..."}
                    style={{ flex: "1 1 200px", minWidth: 0, padding: "8px 10px", border: "1px solid #cbd5e1", borderRadius: 9 }}
                  />
                  <button className="ops-button" disabled={keyBusy !== null || !(keyDrafts[item.provider] || "").trim()} onClick={() => void saveKey(item.provider)}>
                    <KeyRound size={14}/>{keyBusy === item.provider ? "Đang kiểm tra…" : "Lưu & kiểm tra"}
                  </button>
                  {item.company_key && (
                    <button className="ops-button danger" disabled={keyBusy !== null} onClick={() => void removeKey(item.provider)}><Trash2 size={14}/>Xoá</button>
                  )}
                </div>
              )}
            </div>
          ))}
          <p className="ops-muted" style={{ fontSize: ".7rem", marginTop: 10 }}>Nhúng tài liệu (RAG) vẫn dùng key hệ thống để các vector đã lưu còn so khớp được.</p>
        </section>
      </div>

      {canConfigureAgents && (
        <section className="ops-card ops-section">
          <div className="ops-card-header">
            <h2>Model cho từng agent</h2>
            <span style={{ display: "flex", gap: 10, alignItems: "center" }}>
              <button className="ops-button secondary" onClick={() => void loadModels(true)} disabled={modelLoading} title="Lấy lại danh sách model từ nhà cung cấp">
                <RefreshCw size={14} className={modelLoading ? "animate-spin" : ""}/>Làm mới
              </button>
              <Bot size={19} color="#64748b"/>
            </span>
          </div>
          <p className="ops-muted" style={{ fontSize: ".75rem", margin: "0 0 10px" }}>
            Danh sách lấy trực tiếp từ nhà cung cấp bằng key đang dùng (key công ty nếu đã nhập). Đổi model có hiệu lực ngay từ lượt chat tiếp theo.
          </p>
          {modelError && <div className="ops-alert error">{modelError}</div>}
          {modelOptions && Object.keys(modelOptions.errors || {}).length > 0 && (
            <div className="ops-alert error">
              Không đọc được danh sách model của {Object.keys(modelOptions.errors).map((key) => PROVIDER_LABELS[key] || key).join(", ")}; chỉ hiện model mặc định của nhà cung cấp đó.
            </div>
          )}
          {modelLoading && !modelOptions ? (
            <div className="ops-muted" style={{ fontSize: ".8rem" }}>Đang lấy danh sách model…</div>
          ) : (
            agents.map((agent) => (
              <div key={agent.role_code} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap", padding: "10px 0", borderTop: "1px solid #eef2f7" }}>
                <span><strong>{agent.name}</strong><br/><small className="ops-muted">{agent.role_code}{agent.is_active ? "" : " · đang tắt"}</small></span>
                <select
                  value={agent.model_name || ""}
                  disabled={savingAgent !== null || !modelOptions}
                  onChange={(e) => void saveAgentModel(agent.role_code, e.target.value)}
                  style={{ flex: "0 1 340px", minWidth: 0, padding: "8px 10px", border: "1px solid #cbd5e1", borderRadius: 9, background: "#fff" }}
                >
                  <option value="">Mặc định hệ thống{modelOptions?.default ? ` (${modelOptions.default.id})` : ""}</option>
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
                  {agent.model_name && !modelOptions?.models.some((model) => model.id === agent.model_name) && (
                    <option value={agent.model_name}>{agent.model_name} (không có trong danh sách hiện tại)</option>
                  )}
                </select>
              </div>
            ))
          )}
        </section>
      )}

      <section className="ops-card ops-section">
        <div className="ops-card-header"><h2>Export hoặc xóa dữ liệu</h2><Download size={19} color="#64748b"/></div>
        <div className="ops-grid ops-grid-2">
          <div>
            <h3>Export an toàn</h3>
            <p className="ops-muted" style={{ fontSize: ".78rem" }}>Xuất workspace, phòng ban, thành viên, agents, tasks, workflows và scope integration. Không bao gồm password hash hoặc credential reference.</p>
            <button className="ops-button secondary" onClick={() => void exportData()}><Download size={15}/>Export JSON</button>
          </div>
          <div style={{ borderLeft: "1px solid #fee2e2", paddingLeft: 20 }}>
            <h3 style={{ color: "#b91c1c" }}>Yêu cầu xóa workspace</h3>
            <div className="ops-form-grid" style={{ marginTop: 10 }}>
              <div className="ops-field full"><label>Nhập chính xác domain: {workspace.domain}</label><input disabled={!canRequestDeletion} value={confirmation} onChange={(e) => setConfirmation(e.target.value)}/></div>
              <div className="ops-field full"><label>Lý do</label><textarea disabled={!canRequestDeletion} value={deletionReason} onChange={(e) => setDeletionReason(e.target.value)} rows={3}/></div>
              <div className="ops-field full"><button className="ops-button danger" disabled={!canRequestDeletion || confirmation !== workspace.domain || deletionReason.length < 10} onClick={() => void requestDeletion()}><Trash2 size={15}/>Gửi yêu cầu review</button></div>
            </div>
          </div>
        </div>
      </section>
    </AdminShell>
  );
}
