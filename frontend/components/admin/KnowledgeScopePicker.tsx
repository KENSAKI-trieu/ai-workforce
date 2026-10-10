"use client";

import { useMemo, useState, type ReactNode } from "react";
import { AlertTriangle, ChevronDown, ChevronRight, FileText, Layers3, LockKeyhole, Search } from "lucide-react";

export interface ChunkOption {
  id: string;
  chunk_index: number;
  section_title: string;
  page_start: number | null;
  page_end: number | null;
  status: string;
  confidentiality: string;
}

export interface DocumentOption {
  document_id: string;
  document_name: string;
  document_title: string;
  collection_name: string;
  department_access: string;
  confidentiality: string;
  status: string;
  chunks: ChunkOption[];
}

/** Ticks only: "*" and "none" are stored spellings, not boxes anyone can tick. */
export function tickedSelectors(values: string[] | null | undefined): string[] {
  return (values || [])
    .filter((value) => value !== "*" && value !== "none")
    .map((value) => (value.includes(":") ? value : `collection:${value}`));
}

function pageLabel(start: number | null, end: number | null) {
  if (start == null) return "Không rõ trang";
  return end != null && end !== start ? `Trang ${start}–${end}` : `Trang ${start}`;
}

interface KnowledgeScopePickerProps {
  icon: ReactNode;
  title: string;
  description: string;
  /** What the agent does with nothing ticked, shown as a warning. */
  emptyWarning: string;
  documents: DocumentOption[];
  selected: string[];
  onChange: (next: string[]) => void;
  /** Selectors still held for knowledge deleted since; listed so they can be unticked. */
  orphaned?: string[];
}

/** Collections, documents and chunks an agent may read: exactly what is ticked here. */
export default function KnowledgeScopePicker({
  icon,
  title,
  description,
  emptyWarning,
  documents,
  selected,
  onChange,
  orphaned = [],
}: KnowledgeScopePickerProps) {
  const [query, setQuery] = useState("");
  const [openCollections, setOpenCollections] = useState<Set<string>>(new Set());
  const [openDocuments, setOpenDocuments] = useState<Set<string>>(new Set());

  const collections = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase("vi");
    const grouped = new Map<string, DocumentOption[]>();
    for (const document of documents) {
      const searchable = [
        document.document_name,
        document.document_title,
        document.document_id,
        document.collection_name,
      ].join(" ").toLocaleLowerCase("vi");
      if (needle && !searchable.includes(needle)) continue;
      const items = grouped.get(document.collection_name) || [];
      items.push(document);
      grouped.set(document.collection_name, items);
    }
    return [...grouped.entries()].sort(([left], [right]) => left.localeCompare(right, "vi"));
  }, [documents, query]);

  const toggle = (selector: string, inherited = false) => {
    if (inherited) return;
    onChange(
      selected.includes(selector)
        ? selected.filter((item) => item !== selector)
        : [...selected, selector].sort(),
    );
  };

  const flip = (setter: React.Dispatch<React.SetStateAction<Set<string>>>, key: string) => {
    setter((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const live = selected.filter((item) => !orphaned.includes(item));

  return (
    <section className="ta-card" style={{ padding: 20 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, marginBottom: 12 }}>
        <div>
          <h2 style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 16, fontWeight: 800 }}>{icon} {title}</h2>
          <p style={{ marginTop: 3, color: "var(--text-muted)", fontSize: 11 }}>{description}</p>
        </div>
        <span className={`ta-badge ${live.length ? "ta-badge-info" : "ta-badge-warning"}`} style={{ flexShrink: 0, whiteSpace: "nowrap" }}>
          {live.length ? `${live.length} phạm vi` : "Chưa tích"}
        </span>
      </div>

      {live.length === 0 && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "9px 11px", marginBottom: 12, borderRadius: 9, background: "#FFFBEB", color: "#92400E", fontSize: 12 }}>
          <AlertTriangle size={15} /> {emptyWarning}
        </div>
      )}

      <div style={{ border: "1px solid var(--border)", borderRadius: 11, overflow: "hidden" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, padding: 10, borderBottom: "1px solid var(--border)", background: "#F8FAFC" }}>
          <Search size={15} color="var(--text-muted)" />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Tìm collection hoặc tài liệu..." style={{ flex: 1, border: 0, outline: 0, background: "transparent", fontSize: 12 }} />
        </div>
        <div style={{ maxHeight: 520, overflowY: "auto" }}>
          {orphaned.length > 0 && (
            <div style={{ borderBottom: "1px solid var(--border)", background: "#FFFBEB", padding: "10px 12px" }}>
              <strong style={{ display: "block", fontSize: 12, color: "#92400E" }}>Phạm vi trỏ tới tài liệu đã bị xoá</strong>
              <span style={{ display: "block", marginTop: 2, color: "#92400E", fontSize: 10.5 }}>Không còn khớp tài liệu nào. Bỏ chọn để gỡ khỏi cấu hình.</span>
              <div style={{ display: "grid", gap: 5, marginTop: 8 }}>
                {orphaned.map((selector) => (
                  <label key={selector} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11, cursor: "pointer" }}>
                    <input type="checkbox" checked={selected.includes(selector)} onChange={() => toggle(selector)} />
                    <FileText size={13} color="#B45309" />
                    <span style={{ overflowWrap: "anywhere" }}>{selector.replace(/^(document|chunk|collection):/, "")}</span>
                    <span style={{ color: "#B45309", fontSize: 10 }}>đã bị xoá</span>
                  </label>
                ))}
              </div>
            </div>
          )}
          {collections.length === 0 && <p style={{ padding: 20, color: "var(--text-muted)", fontSize: 12, textAlign: "center" }}>Không tìm thấy tài liệu.</p>}
          {collections.map(([collectionName, items]) => {
            const collectionSelector = `collection:${collectionName}`;
            const collectionSelected = selected.includes(collectionSelector);
            const collectionOpen = openCollections.has(collectionName) || Boolean(query.trim());
            return (
              <div key={collectionName} style={{ borderBottom: "1px solid var(--border)" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "10px 12px", background: collectionSelected ? "#F0FDF4" : "#fff" }}>
                  <button type="button" aria-label="Mở collection" onClick={() => flip(setOpenCollections, collectionName)} style={{ display: "grid", placeItems: "center", padding: 0, border: 0, background: "transparent", cursor: "pointer" }}>{collectionOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}</button>
                  <input type="checkbox" checked={collectionSelected} onChange={() => toggle(collectionSelector)} />
                  <Layers3 size={15} color="#6366F1" />
                  <strong style={{ flex: 1, fontSize: 12 }}>{collectionName}</strong>
                  <span style={{ color: "var(--text-muted)", fontSize: 10.5 }}>{items.length} tài liệu</span>
                </div>
                {collectionOpen && items.map((document) => {
                  const documentSelector = `document:${document.document_id}`;
                  const documentDirect = selected.includes(documentSelector);
                  const documentInherited = collectionSelected;
                  const documentOpen = openDocuments.has(document.document_id);
                  return (
                    <div key={document.document_id} style={{ borderTop: "1px solid #F1F5F9", background: "#FAFBFC" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "9px 12px 9px 38px" }}>
                        <button type="button" aria-label="Mở danh sách chunk" onClick={() => flip(setOpenDocuments, document.document_id)} style={{ display: "grid", placeItems: "center", padding: 0, border: 0, background: "transparent", cursor: "pointer" }}>{documentOpen ? <ChevronDown size={15} /> : <ChevronRight size={15} />}</button>
                        <input type="checkbox" checked={documentDirect || documentInherited} disabled={documentInherited} onChange={() => toggle(documentSelector, documentInherited)} />
                        <FileText size={14} color="#64748B" />
                        <span style={{ minWidth: 0, flex: 1 }}><strong style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 11.5 }}>{document.document_title}</strong><span style={{ display: "block", marginTop: 2, color: "var(--text-muted)", fontSize: 10 }}>{document.department_access} · {document.confidentiality} · {document.chunks.length} chunks</span></span>
                        {documentInherited && <span title="Được cấp từ collection" style={{ color: "#047857" }}><LockKeyhole size={13} /></span>}
                      </div>
                      {documentOpen && <div style={{ padding: "0 12px 9px 76px", display: "grid", gap: 5 }}>{document.chunks.map((chunk) => {
                        const chunkSelector = `chunk:${chunk.id}`;
                        const inherited = documentInherited || documentDirect;
                        const checked = inherited || selected.includes(chunkSelector);
                        return <label key={chunk.id} style={{ display: "flex", alignItems: "center", gap: 8, padding: "7px 8px", borderRadius: 7, background: checked ? "#F0FDF4" : "#fff", color: inherited ? "var(--text-muted)" : "var(--text-dark)", cursor: inherited ? "default" : "pointer" }}><input type="checkbox" checked={checked} disabled={inherited} onChange={() => toggle(chunkSelector, inherited)} /><span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 10.5 }}>#{chunk.chunk_index} · {chunk.section_title}</span><span style={{ color: "var(--text-muted)", fontSize: 9.5 }}>{pageLabel(chunk.page_start, chunk.page_end)}</span></label>;
                      })}</div>}
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
