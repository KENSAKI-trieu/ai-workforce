"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { FileText, Globe2, NotepadText, Trash2, Upload } from "lucide-react";

import api from "@/lib/api";
import { useAuthStore } from "@/store/useAuthStore";
import { useKnowledgeWorkflowStore } from "@/store/useKnowledgeWorkflowStore";
import KnowledgeShell from "../_components/KnowledgeShell";
import WizardHeader from "../_components/WizardHeader";
import type { Department } from "../_lib/types";
import { formatFileSize, messageFrom } from "../_lib/utils";
import styles from "../knowledge.module.css";

const acceptedExtensions = ["pdf", "docx", "txt", "md", "csv"];

export default function NewKnowledgePage() {
  const router = useRouter();
  const { hasHydrated, isAuthenticated } = useAuthStore();
  const workflow = useKnowledgeWorkflowStore();
  const [file, setFile] = useState<File | null>(workflow.file);
  const [collection, setCollection] = useState(workflow.collection);
  const [department, setDepartment] = useState(workflow.department);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!hasHydrated || !isAuthenticated) return;
    api.get<Department[]>("/api/v1/workspace/departments")
      .then(({ data }) => setDepartments(data))
      .catch((reason) => setError(messageFrom(reason)));
  }, [hasHydrated, isAuthenticated]);

  const selectFile = (nextFile: File | null) => {
    setError(null);
    if (!nextFile) {
      setFile(null);
      return;
    }
    const extension = nextFile.name.split(".").pop()?.toLowerCase();
    if (!extension || !acceptedExtensions.includes(extension)) {
      setError("Định dạng chưa được hỗ trợ. Vui lòng chọn PDF, DOCX, TXT, MD hoặc CSV.");
      return;
    }
    if (nextFile.size > 10 * 1024 * 1024) {
      setError("Tài liệu vượt quá giới hạn 10 MB.");
      return;
    }
    setFile(nextFile);
  };

  const continueToChunking = () => {
    if (!file) return;
    workflow.setSource(file, collection.trim() || "General Knowledge", department);
    router.push("/knowledge/chunking");
  };

  return (
    <KnowledgeShell>
      <WizardHeader current={1} />
      <main className={styles.wizardMain}>
        <div className={styles.wizardTitle}>
          <h1>Chọn nguồn dữ liệu</h1>
          <p>Tải lên một file để cấu hình cách chia chunk trước khi lập chỉ mục.</p>
        </div>
        {error && <div className={styles.error}>{error}</div>}
        <section className={`${styles.wizardCard} ta-card`}>
          <div className={styles.sourceOptions}>
            <div className={`${styles.sourceOption} ${styles.sourceOptionActive}`}><FileText size={20} /> <strong>Nhập từ tệp văn bản</strong></div>
            <div className={styles.sourceOption} aria-disabled="true"><NotepadText size={20} /> Đồng bộ từ Notion</div>
            <div className={styles.sourceOption} aria-disabled="true"><Globe2 size={20} /> Đồng bộ từ trang web</div>
          </div>

          <label
            className={`${styles.dropzone} ${file ? styles.dropzoneActive : ""}`}
            htmlFor="knowledge-source-file"
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => {
              event.preventDefault();
              selectFile(event.dataTransfer.files?.[0] || null);
            }}
          >
            <input id="knowledge-source-file" type="file" accept=".pdf,.docx,.txt,.md,.csv" hidden onChange={(event) => selectFile(event.target.files?.[0] || null)} />
            {file ? (
              <span className={styles.selectedFile}>
                <span className={styles.fileIcon}><FileText size={22} /></span>
                <span><strong>{file.name}</strong><small>{formatFileSize(file.size)} · sẵn sàng cấu hình</small></span>
                <button type="button" className="ta-btn ta-btn-ghost" aria-label="Bỏ file đã chọn" onClick={(event) => { event.preventDefault(); selectFile(null); }}><Trash2 size={15} /></button>
              </span>
            ) : (
              <>
                <span className={styles.dropIcon}><Upload size={22} /></span>
                <strong>Kéo thả tài liệu vào đây</strong>
                <span>hoặc bấm để chọn · PDF, DOCX, TXT, MD, CSV · tối đa 10 MB</span>
              </>
            )}
          </label>

          <div className={styles.formGrid}>
            <div className={styles.field}>
              <label htmlFor="knowledge-collection">Collection</label>
              <input id="knowledge-collection" className="ta-input" value={collection} onChange={(event) => setCollection(event.target.value)} />
            </div>
            <div className={styles.field}>
              <label htmlFor="knowledge-department">Phạm vi truy cập</label>
              <select id="knowledge-department" className="ta-input" value={department} onChange={(event) => setDepartment(event.target.value)}>
                <option value="ALL">Toàn công ty</option>
                {departments.map((item) => <option key={item.id} value={item.code}>{item.name} ({item.code})</option>)}
              </select>
            </div>
          </div>

          <div className={styles.actions}>
            <button type="button" className="ta-btn ta-btn-ghost" onClick={() => router.push("/knowledge")}>Hủy</button>
            <button type="button" className="ta-btn ta-btn-primary" disabled={!file} onClick={continueToChunking}>Tiếp theo</button>
          </div>
        </section>
      </main>
    </KnowledgeShell>
  );
}
