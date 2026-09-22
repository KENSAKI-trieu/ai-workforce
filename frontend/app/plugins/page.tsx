"use client";

/**
 * Plugin management.
 *
 * The preview panel is the point of this page rather than a decoration: a package's
 * `append` text means nothing on its own, and reviewing the YAML does not tell an
 * operator what the model will actually be sent. Installing without seeing the result
 * is how a tenant ends up with a prompt nobody has read end to end.
 */

import { useCallback, useEffect, useState } from "react";
import Sidebar from "@/components/Sidebar";
import api from "@/lib/api";
import PluginEditor from "./PluginEditor";
import {
  AlertTriangle,
  Check,
  Eye,
  Package,
  Pencil,
  Plus,
  RefreshCw,
  ShieldOff,
  Trash,
  Trash2,
} from "lucide-react";

interface PluginManifest {
  name: string;
  version: string;
  display_name: string;
  description: string;
  target_role: string;
  prompt_slots: string[];
  prompt_modes: Record<string, string>;
  restricts_tools: boolean;
  tools_access: string[];
  disallowed_actions: string[];
  knowledge: string[];
  // False for packages shipped in the repository: those change with a release, not here.
  editable: boolean;
}

interface InstalledPlugin {
  plugin_name: string;
  plugin_version: string;
  target_role: string;
  installed_at: string;
  disk_version: string | null;
  drifted: boolean;
  missing_on_disk: boolean;
}

interface PromptPreview {
  role_code: string;
  slot: string;
  is_overridden: boolean;
  default_prompt: string;
  resolved_prompt: string;
  contributing_plugins: string[];
  has_tenant_text: boolean;
}

const SLOT_LABELS: Record<string, string> = {
  classifier: "Định tuyến ý định",
  answer: "Sinh câu trả lời",
  leave_slot: "Bóc tham số đơn nghỉ",
  leave_draft: "Đọc lượt trả lời đơn nghỉ",
  legal_classifier: "Định tuyến ý định (Legal)",
  legal_perspective: "Đọc góc nhìn bên A/B",
};

const SLOT_WARNINGS: Record<string, string> = {
  classifier:
    "Slot này quyết định nhánh nghiệp vụ nào được chạy. Sửa sai sẽ làm agent đi nhầm nhánh, không chỉ đổi giọng văn.",
  leave_slot:
    "Slot này bóc ngày tháng và lý do nghỉ. Sửa sai sẽ làm đơn nghỉ ghi sai ngày.",
  leave_draft:
    "Slot này quyết định một câu trả lời giữa chừng là tiếp tục, hủy hay chuyện khác. Sửa sai sẽ làm đơn nghỉ bị hủy nhầm hoặc ghi nhầm lý do.",
  legal_classifier:
    "Slot này quyết định Legal Agent rà soát hợp đồng hay trả lời câu hỏi. Sửa sai sẽ làm agent bỏ qua hợp đồng cần rà soát.",
  legal_perspective:
    "Slot này đọc người dùng đại diện bên nào. Sửa sai sẽ làm điểm rủi ro bị đảo chiều.",
};

export default function PluginsPage() {
  const [catalogue, setCatalogue] = useState<PluginManifest[]>([]);
  const [installed, setInstalled] = useState<InstalledPlugin[]>([]);
  const [preview, setPreview] = useState<PromptPreview | null>(null);
  const [previewSlot, setPreviewSlot] = useState("answer");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // null = editor closed, "" = writing a new package, a name = editing that one.
  const [editing, setEditing] = useState<string | null>(null);
  // The package whose delete is waiting to be confirmed.
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const installedNames = new Set(installed.map((row) => row.plugin_name));

  const load = useCallback(async () => {
    setError(null);
    try {
      const [cat, inst] = await Promise.all([
        api.get<PluginManifest[]>("/api/v1/plugins/"),
        api.get<InstalledPlugin[]>("/api/v1/plugins/installed"),
      ]);
      setCatalogue(cat.data);
      setInstalled(inst.data);
    } catch {
      setError("Không tải được danh sách plugin.");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadPreview = useCallback(async (slot: string) => {
    try {
      const res = await api.get<PromptPreview>(`/api/v1/plugins/preview/HR/${slot}`);
      setPreview(res.data);
    } catch {
      setPreview(null);
    }
  }, []);

  // Deferred a tick, matching the other admin pages: the lint rule forbids a fetch that
  // calls setState from running synchronously inside an effect body.
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadPreview(previewSlot), 0);
    return () => window.clearTimeout(timer);
  }, [previewSlot, loadPreview, installed]);

  const install = async (name: string) => {
    setBusy(name);
    setError(null);
    try {
      await api.post(`/api/v1/plugins/${name}/install`);
      await load();
    } catch {
      setError(`Không cài được plugin ${name}.`);
    } finally {
      setBusy(null);
    }
  };

  const removePackage = async (name: string) => {
    setBusy(name);
    setError(null);
    try {
      await api.delete(`/api/v1/plugins/authored/${name}`);
      await load();
    } catch (reason) {
      const detail = (reason as { response?: { data?: { detail?: string } } })?.response
        ?.data?.detail;
      setError(typeof detail === "string" ? detail : `Không xoá được gói ${name}.`);
    } finally {
      setBusy(null);
      setConfirmDelete(null);
    }
  };

  const uninstall = async (name: string) => {
    setBusy(name);
    setError(null);
    try {
      await api.delete(`/api/v1/plugins/${name}`);
      await load();
    } catch {
      setError(`Không gỡ được plugin ${name}.`);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "var(--body-bg)" }}>
      <Sidebar />

      {/* minWidth 0 keeps the preview <pre> from widening the column and pushing the
          install buttons past the right edge of the viewport. */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <header className="ta-topbar">
          <div className="breadcrumb">
            <span>Home</span>
            <span className="breadcrumb-sep">›</span>
            <span className="breadcrumb-current">Plugin Prompt &amp; Skill</span>
          </div>
          <button
            className="ta-btn ta-btn-ghost"
            onClick={() => void load()}
            disabled={loading}
          >
            <RefreshCw size={15} className={loading ? "animate-spin" : ""} /> Làm mới
          </button>
        </header>

        <main className="px-5 py-6 sm:px-6 lg:px-8 lg:py-8">
          <div className="mb-7 flex items-start gap-4">
            <span
              className="grid h-11 w-11 shrink-0 place-items-center rounded-xl text-white"
              style={{
                background: "linear-gradient(135deg, #3C50E0, #8B5CF6)",
                boxShadow: "0 4px 12px rgba(60,80,224,0.28)",
              }}
            >
              <Package size={22} />
            </span>
            <div className="min-w-0">
              <h1 className="text-xl font-extrabold text-slate-900">
                Plugin Prompt &amp; Skill
              </h1>
              <p className="mt-1 text-sm text-slate-500">
                Gói cấu hình riêng cho từng công ty. Plugin chỉ có thể thu hẹp quyền của AI
                Employee, không bao giờ cấp thêm quyền.
              </p>
            </div>
          </div>

          {error && (
            <div
              role="alert"
              className="mb-5 flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700"
            >
              <AlertTriangle size={16} className="shrink-0" /> {error}
            </div>
          )}

          {loading ? (
            <div className="flex items-center gap-2 text-sm text-slate-500">
              <RefreshCw size={16} className="animate-spin" /> Đang tải…
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] xl:gap-8">
              {/* ── Catalogue ── */}
              <section className="min-w-0">
                <div className="mb-3 flex items-center justify-between gap-2">
                  <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                    Gói có sẵn
                  </h2>
                  <div className="flex items-center gap-2">
                    <span className="ta-badge ta-badge-neutral">
                      {installed.length}/{catalogue.length} đã cài
                    </span>
                    <button
                      type="button"
                      onClick={() => setEditing("")}
                      className="flex items-center gap-1.5 rounded-lg bg-[var(--primary)] px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-[var(--primary-hover)]"
                    >
                      <Plus size={14} /> Tạo gói
                    </button>
                  </div>
                </div>

                <div className="space-y-4">
                  {catalogue.length === 0 && (
                    <p className="ta-card p-5 text-sm text-slate-500">
                      Chưa có gói nào trong thư mục{" "}
                      <code className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-700">
                        backend/plugins/
                      </code>
                      .
                    </p>
                  )}

                  {catalogue.map((manifest) => {
                    const isInstalled = installedNames.has(manifest.name);
                    const row = installed.find((r) => r.plugin_name === manifest.name);
                    return (
                      <article key={manifest.name} className="ta-card p-5 sm:p-6">
                        <div className="flex items-start justify-between gap-4">
                          <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-2">
                              <h3 className="font-semibold text-slate-900">
                                {manifest.display_name}
                              </h3>
                              {isInstalled && (
                                <span className="ta-badge ta-badge-success">
                                  <Check size={12} /> Đang dùng
                                </span>
                              )}
                            </div>
                            <p className="mt-1 text-xs text-slate-500">
                              {manifest.name} · v{manifest.version} · {manifest.target_role}
                            </p>
                          </div>

                          <div className="flex shrink-0 items-center gap-1.5">
                          {manifest.editable && (
                            <>
                              <button
                                type="button"
                                onClick={() => setEditing(manifest.name)}
                                title="Sửa gói"
                                aria-label={`Sửa gói ${manifest.name}`}
                                className="rounded-lg border border-slate-200 bg-white p-2 text-slate-600 transition hover:bg-slate-50"
                              >
                                <Pencil size={14} />
                              </button>
                              <button
                                type="button"
                                onClick={() => setConfirmDelete(manifest.name)}
                                disabled={busy === manifest.name || isInstalled}
                                title={
                                  isInstalled
                                    ? "Gỡ khỏi workspace trước khi xoá"
                                    : "Xoá gói"
                                }
                                aria-label={`Xoá gói ${manifest.name}`}
                                className="rounded-lg border border-slate-200 bg-white p-2 text-slate-600 transition hover:bg-slate-50 disabled:opacity-40"
                              >
                                <Trash size={14} />
                              </button>
                            </>
                          )}
                          {isInstalled ? (
                            <button
                              onClick={() => uninstall(manifest.name)}
                              disabled={busy === manifest.name}
                              className="flex shrink-0 items-center gap-1.5 rounded-lg border border-red-200 bg-white px-3.5 py-2 text-xs font-semibold text-red-600 transition hover:bg-red-50 disabled:opacity-50"
                            >
                              <Trash2 size={14} /> Gỡ
                            </button>
                          ) : (
                            <button
                              onClick={() => install(manifest.name)}
                              disabled={busy === manifest.name}
                              className="flex shrink-0 items-center gap-1.5 rounded-lg bg-[var(--primary)] px-3.5 py-2 text-xs font-semibold text-white transition hover:bg-[var(--primary-hover)] disabled:opacity-50"
                            >
                              <Check size={14} /> Cài
                            </button>
                          )}
                          </div>
                        </div>

                        {manifest.description && (
                          <p className="mt-3 text-sm leading-relaxed text-slate-600">
                            {manifest.description}
                          </p>
                        )}

                        <dl className="mt-4 space-y-2.5 border-t border-slate-100 pt-4 text-xs">
                          {manifest.prompt_slots.length > 0 && (
                            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                              <dt className="font-semibold text-slate-500">Prompt</dt>
                              <dd className="flex flex-wrap gap-1.5">
                                {manifest.prompt_slots.map((slot) => (
                                  <span
                                    key={slot}
                                    className="rounded-md bg-indigo-50 px-2.5 py-1 font-medium text-indigo-700"
                                  >
                                    {SLOT_LABELS[slot] ?? slot} ({manifest.prompt_modes[slot]})
                                  </span>
                                ))}
                              </dd>
                            </div>
                          )}

                          {manifest.restricts_tools && (
                            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                              <dt className="flex items-center gap-1 font-semibold text-slate-500">
                                <ShieldOff size={12} /> Chỉ cho phép
                              </dt>
                              <dd className="flex flex-wrap gap-1.5">
                                {manifest.tools_access.map((tool) => (
                                  <code
                                    key={tool}
                                    className="rounded-md bg-slate-100 px-2.5 py-1 text-slate-700"
                                  >
                                    {tool}
                                  </code>
                                ))}
                              </dd>
                            </div>
                          )}

                          {manifest.disallowed_actions.length > 0 && (
                            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                              <dt className="font-semibold text-slate-500">Cấm</dt>
                              <dd className="flex flex-wrap gap-1.5">
                                {manifest.disallowed_actions.map((action) => (
                                  <code
                                    key={action}
                                    className="rounded-md bg-red-50 px-2.5 py-1 text-red-700"
                                  >
                                    {action}
                                  </code>
                                ))}
                              </dd>
                            </div>
                          )}
                        </dl>

                        {row?.drifted && (
                          <p className="mt-3 flex items-start gap-1.5 rounded-lg bg-amber-50 p-2.5 text-xs text-amber-800">
                            <AlertTriangle size={13} className="mt-px shrink-0" />
                            Đã cài v{row.plugin_version}, trên đĩa là v{row.disk_version}. Cài
                            lại để cập nhật bản ghi.
                          </p>
                        )}
                        {row?.missing_on_disk && (
                          <p className="mt-3 flex items-start gap-1.5 rounded-lg bg-red-50 p-2.5 text-xs text-red-700">
                            <AlertTriangle size={13} className="mt-px shrink-0" />
                            Gói không còn trên đĩa; tenant đang chạy prompt mặc định.
                          </p>
                        )}
                      </article>
                    );
                  })}
                </div>
              </section>

              {/* ── Resolved prompt preview ── */}
              <section className="min-w-0">
                <h2 className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  <Eye size={14} /> Prompt thực tế gửi cho model
                </h2>

                <div className="ta-card overflow-hidden xl:sticky xl:top-6">
                  <div className="flex flex-wrap gap-2 border-b border-slate-100 p-4 sm:p-5">
                    {Object.keys(SLOT_LABELS).map((slot) => (
                      <button
                        key={slot}
                        onClick={() => setPreviewSlot(slot)}
                        className={`rounded-lg px-3.5 py-2 text-xs font-semibold transition ${
                          previewSlot === slot
                            ? "bg-[var(--primary)] text-white"
                            : "border border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
                        }`}
                      >
                        {SLOT_LABELS[slot]}
                      </button>
                    ))}
                  </div>

                  {SLOT_WARNINGS[previewSlot] && (
                    <p className="flex items-start gap-1.5 border-b border-slate-100 bg-amber-50 px-4 py-3 sm:px-5 text-xs leading-relaxed text-amber-800">
                      <AlertTriangle size={13} className="mt-px shrink-0" />
                      {SLOT_WARNINGS[previewSlot]}
                    </p>
                  )}

                  {preview ? (
                    <>
                      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-4 py-3 sm:px-5 text-xs">
                        <span
                          className={
                            preview.is_overridden
                              ? "font-semibold text-[var(--primary)]"
                              : "text-slate-500"
                          }
                        >
                          {preview.is_overridden
                            ? `Đã tuỳ biến bởi: ${[
                                ...preview.contributing_plugins,
                                ...(preview.has_tenant_text
                                  ? ["quy ước riêng của công ty"]
                                  : []),
                              ].join(", ")}`
                            : "Đang dùng prompt mặc định"}
                        </span>
                        <span className="shrink-0 text-slate-400">
                          {preview.resolved_prompt.length} ký tự
                        </span>
                      </div>
                      <pre className="max-h-[32rem] overflow-auto whitespace-pre-wrap break-words bg-slate-50 p-4 sm:p-5 font-mono text-xs leading-relaxed text-slate-800">
                        {preview.resolved_prompt}
                      </pre>
                    </>
                  ) : (
                    <p className="p-4 text-sm text-slate-500">
                      Không xem trước được slot này.
                    </p>
                  )}
                </div>
              </section>
            </div>
          )}
          {confirmDelete !== null && (
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
              <div
                role="alertdialog"
                aria-modal="true"
                aria-labelledby="confirm-delete-title"
                className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl"
              >
                <h2
                  id="confirm-delete-title"
                  className="text-base font-semibold text-slate-900"
                >
                  Xoá gói {confirmDelete}?
                </h2>
                <p className="mt-2 text-sm leading-relaxed text-slate-600">
                  Nội dung gói sẽ mất hẳn và không khôi phục được. Nếu cần dùng lại, hãy
                  sao chép manifest ra chỗ khác trước khi xoá.
                </p>
                <div className="mt-5 flex justify-end gap-2">
                  <button
                    type="button"
                    onClick={() => setConfirmDelete(null)}
                    className="rounded-lg border border-slate-200 bg-white px-3.5 py-2 text-xs font-semibold text-slate-700 transition hover:bg-slate-50"
                  >
                    Huỷ
                  </button>
                  <button
                    type="button"
                    onClick={() => removePackage(confirmDelete)}
                    disabled={busy === confirmDelete}
                    className="rounded-lg bg-red-600 px-3.5 py-2 text-xs font-semibold text-white transition hover:bg-red-700 disabled:opacity-50"
                  >
                    Xoá
                  </button>
                </div>
              </div>
            </div>
          )}

          {editing !== null && (
            <PluginEditor
              editingName={editing || null}
              onClose={() => setEditing(null)}
              onSaved={load}
            />
          )}
        </main>
      </div>
    </div>
  );
}
