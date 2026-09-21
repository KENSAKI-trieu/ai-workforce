"use client";

/**
 * Write, edit and delete a plugin package.
 *
 * The manifest is edited as YAML rather than through a form. A form would have to
 * mirror every rule the backend parser already enforces, and the two would drift; here
 * the same parser that runs on save also answers the Kiểm tra button, so what the
 * author sees is what will actually be accepted.
 */

import { useCallback, useEffect, useState } from "react";
import api from "@/lib/api";
import { AlertTriangle, Check, Loader2, X } from "lucide-react";

const STARTER_YAML = `name: ten-goi-cua-ban
version: 1.0.0
display_name: "Tên hiển thị"
description: >-
  Một dòng mô tả gói này làm gì.
target_role: HR

prompts:
  answer:
    mode: append
    text: |
      Quy ước riêng của công ty:
      - Xưng hô với người dùng là "anh/chị".

# Tuỳ chọn: thu hẹp công cụ. Chỉ lấy bớt được, không cấp thêm bao giờ.
# skills:
#   disallowed_actions:
#     - export_hr_directory
`;

interface ValidationResult {
  valid: boolean;
  error?: string;
  manifest?: { name: string; target_role: string; prompt_slots: string[] };
}

function errorFrom(reason: unknown, fallback: string): string {
  const detail = (reason as { response?: { data?: { detail?: string } } })?.response
    ?.data?.detail;
  return typeof detail === "string" ? detail : fallback;
}

export default function PluginEditor({
  editingName,
  onClose,
  onSaved,
}: {
  // null means "write a new package"; a name means "edit that one".
  editingName: string | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [source, setSource] = useState(editingName ? "" : STARTER_YAML);
  const [loading, setLoading] = useState(Boolean(editingName));
  const [saving, setSaving] = useState(false);
  const [check, setCheck] = useState<ValidationResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!editingName) return;
    try {
      const { data } = await api.get<{ source_yaml: string }>(
        `/api/v1/plugins/authored/${editingName}`
      );
      setSource(data.source_yaml);
    } catch (reason) {
      setError(errorFrom(reason, "Không đọc được gói này."));
    } finally {
      setLoading(false);
    }
  }, [editingName]);

  // Deferred a tick, like the other admin pages: the lint rule forbids a fetch that
  // calls setState from running synchronously inside an effect body.
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const validate = async () => {
    setError(null);
    try {
      const { data } = await api.post<ValidationResult>("/api/v1/plugins/validate", {
        source_yaml: source,
      });
      setCheck(data);
    } catch (reason) {
      setError(errorFrom(reason, "Không kiểm tra được."));
    }
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      if (editingName) {
        await api.put(`/api/v1/plugins/authored/${editingName}`, {
          source_yaml: source,
        });
      } else {
        await api.post("/api/v1/plugins/authored", { source_yaml: source });
      }
      onSaved();
      onClose();
    } catch (reason) {
      setError(errorFrom(reason, "Không lưu được gói."));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="flex max-h-[90vh] w-full max-w-3xl flex-col rounded-xl bg-white shadow-xl dark:bg-gray-800">
        <header className="flex items-center justify-between border-b border-gray-200 px-5 py-3 dark:border-gray-700">
          <h2 className="font-semibold text-gray-900 dark:text-white">
            {editingName ? `Sửa gói ${editingName}` : "Tạo gói mới"}
          </h2>
          <button type="button" onClick={onClose} aria-label="Đóng">
            <X size={18} />
          </button>
        </header>

        <div className="flex-1 overflow-auto p-5">
          {loading && (
            <p className="mb-3 flex items-center gap-2 text-xs text-gray-500">
              <Loader2 size={14} className="animate-spin" /> Đang tải gói…
            </p>
          )}
          {editingName && (
            <p className="mb-3 flex items-start gap-1 rounded-md bg-amber-50 p-2 text-xs text-amber-800 dark:bg-amber-900/20 dark:text-amber-300">
              <AlertTriangle size={12} className="mt-0.5 shrink-0" />
              Nếu gói này đang được cài, thay đổi có hiệu lực ngay ở lượt chat tiếp theo.
              Tên gói không đổi được.
            </p>
          )}

          <textarea
            value={source}
            onChange={(event) => {
              setSource(event.target.value);
              setCheck(null);
            }}
            spellCheck={false}
            rows={20}
            className="w-full rounded-lg border border-gray-300 p-3 font-mono text-xs leading-relaxed dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100"
          />

          {check && (
            <p
              className={`mt-3 flex items-start gap-1 rounded-md p-2 text-xs ${
                check.valid
                  ? "bg-green-50 text-green-800 dark:bg-green-900/20 dark:text-green-300"
                  : "bg-red-50 text-red-700 dark:bg-red-900/20 dark:text-red-300"
              }`}
            >
              {check.valid ? (
                <Check size={12} className="mt-0.5 shrink-0" />
              ) : (
                <AlertTriangle size={12} className="mt-0.5 shrink-0" />
              )}
              {check.valid
                ? `Hợp lệ — ${check.manifest?.name} cho role ${check.manifest?.target_role}, sửa slot: ${
                    check.manifest?.prompt_slots.join(", ") || "không có"
                  }`
                : check.error}
            </p>
          )}

          {error && (
            <p className="mt-3 flex items-start gap-1 rounded-md bg-red-50 p-2 text-xs text-red-700 dark:bg-red-900/20 dark:text-red-300">
              <AlertTriangle size={12} className="mt-0.5 shrink-0" />
              {error}
            </p>
          )}
        </div>

        <footer className="flex items-center justify-end gap-2 border-t border-gray-200 px-5 py-3 dark:border-gray-700">
          <button
            type="button"
            onClick={validate}
            className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-100 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-700"
          >
            Kiểm tra
          </button>
          <button
            type="button"
            onClick={save}
            disabled={saving || !source.trim()}
            className="flex items-center gap-1 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {saving && <Loader2 size={14} className="animate-spin" />}
            {editingName ? "Lưu" : "Tạo gói"}
          </button>
        </footer>
      </div>
    </div>
  );
}
