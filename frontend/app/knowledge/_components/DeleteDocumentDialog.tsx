"use client";

import { useEffect, useRef } from "react";
import { AlertTriangle, Loader2, Trash2, X } from "lucide-react";

import type { KnowledgeDocument } from "../_lib/types";
import styles from "../knowledge.module.css";

interface DeleteDocumentDialogProps {
  document: KnowledgeDocument | null;
  pending: boolean;
  error: string | null;
  onClose: () => void;
  onConfirm: () => void;
}

export default function DeleteDocumentDialog({
  document,
  pending,
  error,
  onClose,
  onConfirm,
}: DeleteDocumentDialogProps) {
  const cancelButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!document) return;
    cancelButtonRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !pending) onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [document, onClose, pending]);

  if (!document) return null;

  return (
    <div className={styles.deleteBackdrop} role="presentation" onMouseDown={() => !pending && onClose()}>
      <section
        className={styles.deleteDialog}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="delete-document-title"
        aria-describedby="delete-document-description"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className={styles.deleteHeader}>
          <span className={styles.deleteWarningIcon}><AlertTriangle size={22} /></span>
          <div>
            <h2 id="delete-document-title">Xóa tài liệu?</h2>
            <p>Hành động này không thể hoàn tác.</p>
          </div>
          <button type="button" className={styles.iconButton} onClick={onClose} disabled={pending} aria-label="Đóng">
            <X size={17} />
          </button>
        </header>

        <div className={styles.deleteBody}>
          <p id="delete-document-description">
            Tài liệu và tất cả chunk liên quan sẽ bị xóa vĩnh viễn khỏi kho kiến thức.
          </p>
          <strong title={document.document_name}>{document.document_name}</strong>
          {error && <div className={styles.error}>{error}</div>}
        </div>

        <footer className={styles.deleteActions}>
          <button ref={cancelButtonRef} type="button" className="ta-btn ta-btn-ghost" onClick={onClose} disabled={pending}>
            Hủy
          </button>
          <button type="button" className={styles.dangerButton} onClick={onConfirm} disabled={pending}>
            {pending ? <Loader2 className="animate-spin" size={17} /> : <Trash2 size={17} />}
            {pending ? "Đang xóa..." : "Xóa tài liệu"}
          </button>
        </footer>
      </section>
    </div>
  );
}
