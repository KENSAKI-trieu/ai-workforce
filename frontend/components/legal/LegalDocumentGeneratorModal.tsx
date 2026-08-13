"use client";

import axios from "axios";
import { AlertTriangle, CheckCircle2, FileCheck2, Loader2, Send, ShieldCheck, WandSparkles, X } from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";

import styles from "@/app/agents/LEGAL/legal.module.css";
import api from "@/lib/api";

type FieldType = "text" | "textarea" | "date" | "select";

interface TemplateField {
  name: string;
  label: string;
  type: FieldType;
  required: boolean;
  placeholder: string;
  help_text: string;
  full_width: boolean;
  default: string;
  rows?: number;
  options?: Array<{ value: string; label: string }>;
}

interface DocumentTemplate {
  id: string;
  label: string;
  description: string;
  output_description: string;
  clauses: string[];
  fields: TemplateField[];
  required_fields: string[];
}

interface ValidationWarning {
  code: string;
  severity: "HIGH" | "MEDIUM" | "LOW";
  title: string;
  message: string;
  recommendation: string;
}

interface ValidationResult {
  valid: boolean;
  missing_fields: Array<{ name: string; label: string }>;
  warnings: ValidationWarning[];
}

function initialFields(template: DocumentTemplate) {
  return Object.fromEntries(template.fields.map((field) => [field.name, field.default || ""]));
}

async function errorMessage(error: unknown) {
  if (!axios.isAxiosError(error)) return "Không thể xử lý yêu cầu tạo văn bản.";
  const detail = error.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (error.response?.data instanceof Blob) {
    try {
      const payload = JSON.parse(await error.response.data.text()) as { detail?: string };
      if (payload.detail) return payload.detail;
    } catch {
      // The response is not JSON; fall through to Axios' message.
    }
  }
  return error.message;
}

interface SubmittedDraft {
  artifact_id: string;
  status: string;
}

export default function LegalDocumentGeneratorModal({ onClose, onSubmitted }: { onClose: () => void; onSubmitted?: (draft: SubmittedDraft) => void }) {
  const [templates, setTemplates] = useState<DocumentTemplate[]>([]);
  const [documentType, setDocumentType] = useState("");
  const [fields, setFields] = useState<Record<string, string>>({});
  const [outputFormat, setOutputFormat] = useState<"docx" | "pdf">("docx");
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const loadTemplates = async () => {
      try {
        const { data } = await api.get<DocumentTemplate[]>("/api/v1/legal/document-templates");
        if (!active || data.length === 0) return;
        setTemplates(data);
        setDocumentType(data[0].id);
        setFields(initialFields(data[0]));
      } catch (reason) {
        if (active) setError(await errorMessage(reason));
      } finally {
        if (active) setLoading(false);
      }
    };
    void loadTemplates();
    return () => { active = false; };
  }, []);

  const selectedTemplate = useMemo(
    () => templates.find((template) => template.id === documentType),
    [documentType, templates],
  );
  const requiredComplete = selectedTemplate?.required_fields.every((name) => fields[name]?.trim()) ?? false;

  const changeTemplate = (nextType: string) => {
    const template = templates.find((item) => item.id === nextType);
    if (!template) return;
    setDocumentType(nextType);
    setFields(initialFields(template));
    setValidation(null);
    setError(null);
  };

  const changeField = (name: string, value: string) => {
    setFields((current) => ({ ...current, [name]: value }));
    setValidation(null);
    setError(null);
  };

  const generate = async (event: FormEvent) => {
    event.preventDefault();
    if (!selectedTemplate || !requiredComplete || busy) return;
    setBusy(true);
    setError(null);
    try {
      if (!validation) {
        const { data } = await api.post<ValidationResult>("/api/v1/legal/validate-document", {
          document_type: documentType,
          fields,
        });
        setValidation(data);
        if (!data.valid || data.warnings.length > 0) return;
      }

      const { data } = await api.post<SubmittedDraft>("/api/v1/legal/document-drafts", {
        document_type: documentType,
        output_format: outputFormat,
        fields,
      });
      onSubmitted?.(data);
      onClose();
    } catch (reason) {
      setError(await errorMessage(reason));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={styles.modalBackdrop} role="presentation" onMouseDown={(event) => { if (!busy && event.target === event.currentTarget) onClose(); }}>
      <form className={`${styles.modal} ${styles.generatorModal}`} onSubmit={generate} role="dialog" aria-modal="true" aria-labelledby="legal-generator-title">
        <header>
          <div><span><WandSparkles size={19} /></span><div><h2 id="legal-generator-title">Tạo văn bản pháp lý</h2><p>Thu thập dữ liệu → tạo bản nháp → gửi phê duyệt</p></div></div>
          <button type="button" onClick={onClose} disabled={busy} title="Đóng"><X size={17} /></button>
        </header>

        <div className={styles.modalBody}>
          {loading && <div className={styles.modalLoading}><Loader2 className={styles.spin} size={20} /><span>Đang tải bộ câu hỏi pháp lý…</span></div>}
          {!loading && error && <div className={styles.modalError}><AlertTriangle size={16} /><span>{error}</span></div>}

          {!loading && selectedTemplate && (
            <>
              <label className={styles.fullField}>Loại văn bản
                <select value={documentType} onChange={(event) => changeTemplate(event.target.value)}>
                  {templates.map((template) => <option key={template.id} value={template.id}>{template.label}</option>)}
                </select>
              </label>

              <section className={styles.templateIntro}>
                <div><FileCheck2 size={17} /><span><strong>{selectedTemplate.description}</strong><small>Đầu ra: {selectedTemplate.output_description}</small></span></div>
                <div className={styles.clauseChecklist}>{selectedTemplate.clauses.map((clause) => <span key={clause}><CheckCircle2 size={11} />{clause}</span>)}</div>
              </section>

              {selectedTemplate.fields.map((field) => {
                const className = field.full_width ? styles.fullField : undefined;
                return (
                  <label key={field.name} className={className}>
                    {field.label}{field.required && <span className={styles.requiredMark}> *</span>}
                    {field.type === "select" ? (
                      <select value={fields[field.name] || ""} onChange={(event) => changeField(field.name, event.target.value)} required={field.required}>
                        {(field.options || []).map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                      </select>
                    ) : field.type === "textarea" ? (
                      <textarea rows={field.rows || 3} value={fields[field.name] || ""} onChange={(event) => changeField(field.name, event.target.value)} placeholder={field.placeholder} required={field.required} />
                    ) : (
                      <input type={field.type} value={fields[field.name] || ""} onChange={(event) => changeField(field.name, event.target.value)} placeholder={field.placeholder} required={field.required} />
                    )}
                    {field.help_text && <small className={styles.fieldHelp}>{field.help_text}</small>}
                  </label>
                );
              })}

              {validation && (validation.missing_fields.length > 0 || validation.warnings.length > 0) && (
                <section className={styles.validationPanel}>
                  <header><AlertTriangle size={16} /><div><strong>Legal Clause Validator</strong><small>Kiểm tra trước khi tạo bản nháp</small></div></header>
                  {validation.missing_fields.map((item) => <p key={item.name}><strong>Thiếu dữ liệu:</strong> {item.label}</p>)}
                  {validation.warnings.map((warning) => <article key={warning.code}><span>{warning.severity}</span><div><strong>{warning.title}</strong><p>{warning.message}</p><small>Khuyến nghị: {warning.recommendation}</small></div></article>)}
                </section>
              )}

              <div className={styles.formatControl}><span>Định dạng đầu ra</span><div><button type="button" className={outputFormat === "docx" ? styles.selected : ""} onClick={() => setOutputFormat("docx")}>DOCX</button><button type="button" className={outputFormat === "pdf" ? styles.selected : ""} onClick={() => setOutputFormat("pdf")}>PDF</button></div></div>
              <div className={styles.generatorNotice}><ShieldCheck size={14} /><span>Bản nháp được gửi tới CEO/Admin/Owner. Người tạo chỉ tải được bản cuối sau khi được phê duyệt.</span></div>
            </>
          )}
        </div>

        <footer>
          <button type="button" className={styles.secondaryButton} onClick={onClose} disabled={busy}>Hủy</button>
          <button type="submit" className={styles.primaryButton} disabled={busy || loading || !requiredComplete}>
            {busy ? <Loader2 className={styles.spin} size={16} /> : <Send size={16} />}
            {validation?.warnings.length ? "Gửi duyệt với cảnh báo" : "Kiểm tra & gửi phê duyệt"}
          </button>
        </footer>
      </form>
    </div>
  );
}
