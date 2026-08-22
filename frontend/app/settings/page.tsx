"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Building2,
  Download,
  KeyRound,
  Save,
  Shield,
  Trash2,
  Users,
} from "lucide-react";

import AdminShell from "@/components/admin/AdminShell";
import api from "@/lib/api";
import { useAuthStore } from "@/store/useAuthStore";

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
  api_key_status: Record<string, boolean>;
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
  const canEdit = ["Owner", "Admin", "CEO"].includes(user?.role || "");

  const load = useCallback(async () => {
    const [workspaceResponse, departmentResponse] = await Promise.all([
      api.get("/api/v1/workspace"),
      api.get("/api/v1/workspace/departments"),
    ]);
    setWorkspace(workspaceResponse.data);
    setDepartments(departmentResponse.data);
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

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
          <div className="ops-card-header"><h2>API keys</h2><KeyRound size={19} color="#64748b"/></div>
          <div className="ops-alert">Key không được nhập hoặc trả về qua UI. Hãy cấu hình qua environment/secret vault rồi restart backend.</div>
          {Object.entries(workspace.api_key_status).map(([key, configured]) => (
            <div key={key} style={{ display: "flex", justifyContent: "space-between", padding: "10px 0", borderTop: "1px solid #eef2f7" }}>
              <code>{key}</code>
              <span className={`ops-badge ${configured ? "success" : "warning"}`}>{configured ? "Configured" : "Missing"}</span>
            </div>
          ))}
        </section>
      </div>

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
              <div className="ops-field full"><label>Nhập chính xác domain: {workspace.domain}</label><input disabled={user?.role !== "Owner"} value={confirmation} onChange={(e) => setConfirmation(e.target.value)}/></div>
              <div className="ops-field full"><label>Lý do</label><textarea disabled={user?.role !== "Owner"} value={deletionReason} onChange={(e) => setDeletionReason(e.target.value)} rows={3}/></div>
              <div className="ops-field full"><button className="ops-button danger" disabled={user?.role !== "Owner" || confirmation !== workspace.domain || deletionReason.length < 10} onClick={() => void requestDeletion()}><Trash2 size={15}/>Gửi yêu cầu review</button></div>
            </div>
          </div>
        </div>
      </section>
    </AdminShell>
  );
}
