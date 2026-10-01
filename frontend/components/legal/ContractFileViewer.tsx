"use client";

import axios from "axios";
import { AlertTriangle, Download, FileText, Loader2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import api from "@/lib/api";
import styles from "./ContractFileViewer.module.css";

export interface ContractFile {
  key: string;
  label: string;
  url: string;
  filename: string;
  format: string;
}

function messageFrom(error: unknown) {
  if (!axios.isAxiosError(error)) return "Không mở được file.";
  const detail = error.response?.data?.detail;
  return typeof detail === "string" ? detail : error.message;
}

/**
 * The contract file itself, as it will be signed: a PDF in the browser's own viewer, a
 * Word file laid out page by page. The files are fetched with the session's token, so
 * they are never exposed at a URL another person could open.
 */
export default function ContractFileViewer({ title, files, initialKey, onClose }: {
  title: string;
  files: ContractFile[];
  initialKey?: string;
  onClose: () => void;
}) {
  const [activeKey, setActiveKey] = useState(initialKey || files[0]?.key);
  const [blob, setBlob] = useState<Blob | null>(null);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const docxRef = useRef<HTMLDivElement | null>(null);
  const active = files.find((file) => file.key === activeKey) || files[0];

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    let objectUrl: string | null = null;
    const load = async () => {
      setLoading(true);
      setError(null);
      setBlob(null);
      setPdfUrl(null);
      try {
        const response = await api.get<Blob>(active.url, { responseType: "blob" });
        if (cancelled) return;
        const data = active.format === "pdf" ? new Blob([response.data], { type: "application/pdf" }) : response.data;
        setBlob(data);
        if (active.format === "pdf") {
          objectUrl = URL.createObjectURL(data);
          setPdfUrl(objectUrl);
        } else if (docxRef.current) {
          const { renderAsync } = await import("docx-preview");
          if (cancelled || !docxRef.current) return;
          docxRef.current.innerHTML = "";
          await renderAsync(data, docxRef.current, undefined, {
            className: "docx",
            inWrapper: true,
            ignoreLastRenderedPageBreak: true,
            renderHeaders: true,
            renderFooters: true,
            breakPages: true,
          });
        }
      } catch (reason) {
        if (!cancelled) setError(messageFrom(reason));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [active]);

  const download = () => {
    if (!blob || !active) return;
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = active.filename;
    link.click();
    URL.revokeObjectURL(objectUrl);
  };

  return <div className={styles.backdrop} role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section className={styles.modal} role="dialog" aria-modal="true" aria-label={title}>
      <header>
        <div><span><FileText size={18} /></span><div><strong>{title}</strong><small>{active?.filename} · {active?.format.toUpperCase()}</small></div></div>
        {files.length > 1 && <nav className={styles.tabs}>{files.map((file) => <button key={file.key} type="button" className={file.key === active?.key ? styles.activeTab : ""} onClick={() => setActiveKey(file.key)}>{file.label}</button>)}</nav>}
        <button type="button" className={styles.close} onClick={onClose} aria-label="Đóng"><X size={17} /></button>
      </header>
      <div className={styles.body}>
        {loading && <div className={styles.state}><Loader2 className={styles.spin} size={18} />Đang mở file…</div>}
        {error && <div className={`${styles.state} ${styles.error}`}><AlertTriangle size={17} />{error}</div>}
        {active?.format === "pdf" && pdfUrl && <iframe className={styles.pdf} src={pdfUrl} title={active.filename} />}
        {/* Kept mounted so the Word renderer has somewhere to draw before the file arrives. */}
        <div ref={docxRef} className={styles.docx} hidden={active?.format !== "docx" || Boolean(error)} />
      </div>
      <footer>
        <small>File được tải theo quyền của bạn; không có đường dẫn công khai.</small>
        <button type="button" onClick={download} disabled={!blob}><Download size={14} />Tải {active?.label.toLowerCase()}</button>
        <button type="button" onClick={onClose}>Đóng</button>
      </footer>
    </section>
  </div>;
}
