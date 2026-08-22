"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Boxes, Eye, FileText, Layers3, Loader2, RotateCcw, Save } from "lucide-react";

import api from "@/lib/api";
import { useKnowledgeWorkflowStore } from "@/store/useKnowledgeWorkflowStore";
import KnowledgeShell from "../_components/KnowledgeShell";
import WizardHeader from "../_components/WizardHeader";
import type { ChunkPreview, ChunkingConfig, ChunkingMode } from "../_lib/types";
import { messageFrom } from "../_lib/utils";
import styles from "../knowledge.module.css";

const defaultConfig: ChunkingConfig = { mode: "standard", chunk_size: 700, chunk_overlap: 80, parent_chunk_size: 1024 };

export default function KnowledgeChunkingPage() {
  const router = useRouter();
  const workflow = useKnowledgeWorkflowStore();
  const [config, setConfig] = useState<ChunkingConfig>(workflow.config);
  const [preview, setPreview] = useState<ChunkPreview | null>(workflow.preview);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!workflow.file) router.replace("/knowledge/new");
  }, [router, workflow.file]);

  const validationError = useMemo(() => {
    if (config.chunk_size < 32 || config.chunk_size > 4096) return "Kích thước chunk phải từ 32 đến 4096 token.";
    if (config.chunk_overlap < 0 || config.chunk_overlap >= config.chunk_size) return "Overlap phải nhỏ hơn kích thước chunk.";
    if (config.mode === "parent_child" && (config.parent_chunk_size <= config.chunk_size || config.parent_chunk_size > 8192)) {
      return "Parent chunk phải lớn hơn child chunk và không vượt quá 8192 token.";
    }
    return null;
  }, [config]);

  const updateConfig = (patch: Partial<ChunkingConfig>) => {
    setConfig((current) => ({ ...current, ...patch }));
    setPreview(null);
    setError(null);
  };

  const requestPreview = async () => {
    if (!workflow.file || validationError) {
      setError(validationError || "Vui lòng chọn lại file nguồn.");
      return;
    }
    setPreviewBusy(true);
    setError(null);
    try {
      const body = new FormData();
      body.append("file", workflow.file);
      body.append("chunking_mode", config.mode);
      body.append("chunk_size", String(config.chunk_size));
      body.append("chunk_overlap", String(config.chunk_overlap));
      body.append("parent_chunk_size", String(config.parent_chunk_size));
      const { data } = await api.post<ChunkPreview>("/api/v1/documents/preview-chunks", body);
      setPreview(data);
      workflow.setPreview(data);
    } catch (reason) {
      setError(messageFrom(reason));
    } finally {
      setPreviewBusy(false);
    }
  };

  const saveAndProcess = () => {
    if (validationError) {
      setError(validationError);
      return;
    }
    workflow.setConfig(config);
    if (preview) workflow.setPreview(preview);
    router.push("/knowledge/pipeline");
  };

  if (!workflow.file) return null;

  return (
    <KnowledgeShell>
      <WizardHeader current={2} />
      <main className={styles.wizardMain}>
        <div className={styles.wizardTitle}>
          <h1>Cấu hình phân đoạn</h1>
          <p>Chọn chiến lược chunk, token và overlap; xem thử kết quả trước khi embedding.</p>
        </div>
        {error && <div className={styles.error}>{error}</div>}
        <div className={styles.configLayout}>
          <section className={`${styles.wizardCard} ${styles.configPanel} ta-card`}>
            <div className={styles.pipelineFile} style={{ marginBottom: 0 }}>
              <span className={styles.fileIcon}><FileText size={21} /></span>
              <div><strong>{workflow.file.name}</strong><small>{workflow.collection} · {workflow.department}</small></div>
            </div>

            <div className={styles.field}>
              <label>Kiểu phân đoạn</label>
              <div className={styles.modeGrid}>
                <ModeCard mode="standard" selected={config.mode === "standard"} icon={<Boxes size={21} />} title="Từng đoạn" description="Mỗi chunk độc lập, có kích thước token và overlap cố định." onSelect={() => updateConfig({ mode: "standard" })} />
                <ModeCard mode="parent_child" selected={config.mode === "parent_child"} icon={<Layers3 size={21} />} title="Cha – con" description="Child chunk dùng để truy xuất, parent chunk giữ ngữ cảnh rộng hơn." onSelect={() => updateConfig({ mode: "parent_child" })} />
              </div>
            </div>

            <div className={styles.inputGrid}>
              {config.mode === "parent_child" && (
                <NumberField label="Parent chunk (token)" value={config.parent_chunk_size} min={64} max={8192} onChange={(value) => updateConfig({ parent_chunk_size: value })} />
              )}
              <NumberField label={config.mode === "parent_child" ? "Child chunk (token)" : "Kích thước chunk (token)"} value={config.chunk_size} min={32} max={4096} onChange={(value) => updateConfig({ chunk_size: value })} />
              <NumberField label="Overlap (token)" value={config.chunk_overlap} min={0} max={Math.max(0, config.chunk_size - 1)} onChange={(value) => updateConfig({ chunk_overlap: value })} />
            </div>

            <div className={styles.hint}>
              {config.mode === "parent_child"
                ? "Khuyến nghị: parent 1024 token, child 256–512 token và overlap khoảng 10–20% kích thước child."
                : "Khuyến nghị: 500–800 token và overlap 50–100 token cho tài liệu chính sách, quy trình."}
            </div>

            <div className={styles.actions} style={{ justifyContent: "space-between", marginTop: 0 }}>
              <button type="button" className="ta-btn ta-btn-ghost" onClick={() => { setConfig(defaultConfig); setPreview(null); }}><RotateCcw size={15} /> Đặt lại</button>
              <span style={{ display: "flex", gap: 9 }}>
                <button type="button" className="ta-btn ta-btn-outline" disabled={previewBusy || Boolean(validationError)} onClick={() => void requestPreview()}>
                  {previewBusy ? <Loader2 className="animate-spin" size={15} /> : <Eye size={15} />} {previewBusy ? "Đang tạo bản xem trước..." : "Xem trước chunk"}
                </button>
                <button type="button" className="ta-btn ta-btn-primary" disabled={Boolean(validationError)} onClick={saveAndProcess}><Save size={15} /> Lưu và xử lý</button>
              </span>
            </div>
          </section>

          <section className={`${styles.previewPanel} ta-card`}>
            <header className={styles.previewHeader}>
              <div><strong>Xác nhận & xem trước</strong><div style={{ color: "var(--text-muted)", fontSize: 11, marginTop: 3 }}>{workflow.file.name}</div></div>
              {preview && <span className="ta-badge ta-badge-info">Ước tính {preview.estimated_chunk_count} chunks</span>}
            </header>
            {preview ? (
              <div className={styles.previewList}>
                {preview.chunks.slice(0, 8).map((chunk) => (
                  <article className={styles.chunkCard} key={chunk.chunk_index}>
                    <div className={styles.chunkMeta}><span>Chunk {chunk.chunk_index + 1}</span><span>{chunk.token_count ?? "—"} token</span></div>
                    <h3>{chunk.section_title || "Không có tiêu đề"}</h3>
                    {chunk.parent_chunk_index != null && <div className={styles.parentContext}>Parent #{chunk.parent_chunk_index + 1}</div>}
                    <p>{chunk.content}</p>
                  </article>
                ))}
              </div>
            ) : (
              <div className={styles.previewPlaceholder}><Eye size={28} /><strong>Chưa có bản xem trước</strong><span>Điều chỉnh cấu hình rồi bấm “Xem trước chunk”.</span></div>
            )}
          </section>
        </div>
      </main>
    </KnowledgeShell>
  );
}

function ModeCard({ selected, icon, title, description, onSelect }: { mode: ChunkingMode; selected: boolean; icon: React.ReactNode; title: string; description: string; onSelect: () => void }) {
  return <button type="button" className={`${styles.modeCard} ${selected ? styles.modeCardSelected : ""}`} onClick={onSelect}>{icon}<strong>{title}</strong><span>{description}</span></button>;
}

function NumberField({ label, value, min, max, onChange }: { label: string; value: number; min: number; max: number; onChange: (value: number) => void }) {
  return <div className={styles.field}><label>{label}</label><input className="ta-input" type="number" min={min} max={max} value={value} onChange={(event) => onChange(Number(event.target.value))} /></div>;
}
