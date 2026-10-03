"use client";

import { Calculator, FileSpreadsheet, Loader2, Plus, Trash2, Upload, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import api from "@/lib/api";
import FinanceChart, { type ChartSpec } from "@/components/finance/FinanceChart";
import { apiError, formatVnd } from "@/components/finance/format";

type Kind = "NUMBER" | "DATE" | "TEXT";
interface Column { index: number; name: string; kind: Kind; detected?: Kind; invalid?: number }
interface SheetSummary { sheet_id: string; file: string; sheet: string; uploaded_at?: string | null; columns: { name: string; kind: Kind }[]; row_count: number; rows_left_out_as_totals: number }
interface SheetDetail extends Omit<SheetSummary, "columns"> {
  sheet_names: string[]; header_row: number; skip_totals: boolean; columns: Column[];
  rows: (string | number | null)[][]; skipped_rows: { row: number; reason: string; label?: string }[];
}
interface Filter { column: string; op: "eq" | "contains" | "gte" | "lte"; value: string }
interface Group { group: string; value: string | number | null; rows: number; share_percent?: string | null }
interface Analysis {
  operation: string; value_column: string | null; group_by: string | null; total: string | number | null;
  rows_used: number; rows_left_out_as_totals: number; groups: Group[]; groups_folded_into_other: number; charts: ChartSpec[];
}

const KIND_LABELS: Record<Kind, string> = { NUMBER: "Số", DATE: "Ngày", TEXT: "Chữ" };
const OPERATIONS = [["sum", "Tổng"], ["count", "Đếm số dòng"], ["average", "Trung bình"], ["min", "Nhỏ nhất"], ["max", "Lớn nhất"]] as const;
const OPS: Record<Filter["op"], string> = { eq: "bằng", contains: "có chứa", gte: "từ (≥)", lte: "đến (≤)" };

/**
 * Someone's own spreadsheet: upload it, check how it was read, then compute on it.
 * The server reads the file and does every computation; this page only shows them.
 */
export default function SheetsTab() {
  const [sheets, setSheets] = useState<SheetSummary[]>([]);
  const [selected, setSelected] = useState<SheetDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [operation, setOperation] = useState("sum");
  const [valueColumn, setValueColumn] = useState("");
  const [groupBy, setGroupBy] = useState("");
  const [period, setPeriod] = useState("month");
  const [filters, setFilters] = useState<Filter[]>([]);
  const [result, setResult] = useState<Analysis | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const loadList = useCallback(async () => {
    try {
      const { data } = await api.get<SheetSummary[]>("/api/v1/finance/sheets");
      setSheets(data);
    } catch (reason) {
      setError(apiError(reason).message);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadList(), 0);
    return () => window.clearTimeout(timer);
  }, [loadList]);

  const show = (detail: SheetDetail) => {
    setSelected(detail);
    setResult(null);
    setFilters([]);
    const numbers = detail.columns.filter((column) => column.kind === "NUMBER");
    setValueColumn((current) => numbers.some((column) => column.name === current) ? current : numbers[0]?.name ?? "");
    setGroupBy((current) => detail.columns.some((column) => column.name === current) ? current : "");
  };

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (reason) {
      setError(apiError(reason).message);
    } finally {
      setBusy(false);
    }
  };

  const upload = (file: File) => run(async () => {
    const form = new FormData();
    form.append("file", file);
    const { data } = await api.post<SheetDetail>("/api/v1/finance/sheets", form);
    show(data);
    await loadList();
  });

  const open = (id: string) => run(async () => {
    const { data } = await api.get<SheetDetail>(`/api/v1/finance/sheets/${id}`);
    show(data);
  });

  const correct = (change: { sheet_name?: string; header_row?: number; kinds?: Record<number, Kind>; skip_totals?: boolean }) => run(async () => {
    if (!selected) return;
    const { data } = await api.patch<SheetDetail>(`/api/v1/finance/sheets/${selected.sheet_id}`, change);
    show(data);
    await loadList();
  });

  const remove = () => run(async () => {
    if (!selected || !window.confirm(`Xoá file “${selected.file}”?`)) return;
    await api.delete(`/api/v1/finance/sheets/${selected.sheet_id}`);
    setSelected(null);
    setResult(null);
    await loadList();
  });

  const columnKind = (name: string) => selected?.columns.find((column) => column.name === name)?.kind;
  const groupIsDate = columnKind(groupBy) === "DATE";

  const analyse = () => run(async () => {
    if (!selected) return;
    const { data } = await api.post<Analysis>(`/api/v1/finance/sheets/${selected.sheet_id}/analyze`, {
      operation,
      value_column: operation === "count" ? null : valueColumn || null,
      group_by: groupBy || null,
      period: groupIsDate ? period : null,
      filters: filters.filter((item) => item.column && item.value),
    });
    setResult(data);
  });

  const numberColumns = useMemo(() => selected?.columns.filter((column) => column.kind === "NUMBER") ?? [], [selected]);
  const asValue = (value: string | number | null) => value === null ? "—"
    : result?.operation === "count" ? Number(value).toLocaleString("vi-VN") : formatVnd(value);

  return (
    <div className="stack-mobile" style={{ display: "grid", gridTemplateColumns: "minmax(220px, 280px) minmax(0, 1fr)", gap: 16, alignItems: "start" }}>
      <aside className="ta-card" style={{ padding: 14, display: "grid", gap: 10 }}>
        <input ref={fileInput} type="file" accept=".xlsx,.csv" hidden
          onChange={(event) => { const file = event.target.files?.[0]; if (file) void upload(file); event.target.value = ""; }} />
        <button className="ta-btn ta-btn-primary" disabled={busy} onClick={() => fileInput.current?.click()}>
          {busy ? <Loader2 size={15} className="animate-spin" /> : <Upload size={15} />} Tải file Excel / CSV
        </button>
        <small style={{ color: "var(--text-muted)" }}>
          File của riêng bạn, chỉ bạn xem được, không ghi vào sổ sách. Trợ lý Tài chính dùng được file này khi bạn hỏi trong chat.
        </small>
        {sheets.length === 0 ? <small style={{ color: "var(--text-muted)" }}>Chưa có file nào.</small> : sheets.map((sheet) => (
          <button key={sheet.sheet_id} onClick={() => void open(sheet.sheet_id)}
            className={`ta-btn ${selected?.sheet_id === sheet.sheet_id ? "ta-btn-primary" : "ta-btn-ghost"}`}
            style={{ justifyContent: "flex-start", textAlign: "left", height: "auto", padding: "8px 10px" }}>
            <FileSpreadsheet size={15} />
            <span style={{ display: "grid" }}>
              <span style={{ fontWeight: 600, wordBreak: "break-all" }}>{sheet.file}</span>
              <small>{sheet.sheet} · {sheet.row_count.toLocaleString("vi-VN")} dòng</small>
            </span>
          </button>
        ))}
      </aside>

      <div style={{ display: "grid", gap: 16, minWidth: 0 }}>
        {error && <div className="ta-card" style={{ padding: 12, color: "#B91C1C" }}>{error}</div>}
        {!selected ? (
          <div className="ta-card" style={{ padding: 24, color: "var(--text-muted)" }}>
            Tải lên một bảng tính (chi phí dự án, bảng lương, doanh số...) để tính tổng, đếm, trung bình theo nhóm và xem biểu đồ.
          </div>
        ) : (
          <>
            <section className="ta-card" style={{ padding: 16, display: "grid", gap: 12 }}>
              <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                <strong style={{ marginRight: "auto" }}>Kiểm tra cách đọc “{selected.file}”</strong>
                {selected.sheet_names.length > 1 && (
                  <label style={label}>Sheet
                    <select className="ta-input" value={selected.sheet} onChange={(event) => void correct({ sheet_name: event.target.value })}>
                      {selected.sheet_names.map((name) => <option key={name}>{name}</option>)}
                    </select>
                  </label>
                )}
                <label style={label}>Dòng tiêu đề
                  <input className="ta-input" type="number" min={1} style={{ width: 80 }} defaultValue={selected.header_row} key={`${selected.sheet_id}-${selected.sheet}-${selected.header_row}`}
                    onBlur={(event) => { const row = Number(event.target.value); if (row && row !== selected.header_row) void correct({ header_row: row }); }} />
                </label>
                <label style={label}>
                  <input type="checkbox" checked={selected.skip_totals} onChange={(event) => void correct({ skip_totals: event.target.checked })} />
                  Bỏ dòng Tổng/Cộng
                </label>
                <button className="ta-btn ta-btn-ghost" onClick={() => void remove()} disabled={busy}><Trash2 size={15} /> Xoá file</button>
              </div>

              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                {selected.columns.map((column) => (
                  <span key={column.index} className="ta-badge ta-badge-neutral" style={{ display: "inline-flex", gap: 6, alignItems: "center", padding: "4px 8px" }}>
                    {column.name}
                    <select aria-label={`Kiểu cột ${column.name}`} value={column.kind} style={{ border: "none", background: "transparent", fontSize: 12 }}
                      onChange={(event) => void correct({ kinds: { ...Object.fromEntries(selected.columns.map((item) => [item.index, item.kind])), [column.index]: event.target.value as Kind } })}>
                      {(Object.keys(KIND_LABELS) as Kind[]).map((kind) => <option key={kind} value={kind}>{KIND_LABELS[kind]}</option>)}
                    </select>
                    {!!column.invalid && <small style={{ color: "#B45309" }} title="Ô không đọc được theo kiểu này, bị bỏ qua khi tính">⚠ {column.invalid} ô</small>}
                  </span>
                ))}
              </div>

              {selected.skipped_rows.length > 0 && (
                <small style={{ color: "var(--text-muted)" }}>
                  Đã bỏ {selected.skipped_rows.length} dòng tổng để không cộng hai lần: {selected.skipped_rows.slice(0, 6).map((item) => `dòng ${item.row}${item.label ? ` (“${item.label}”)` : ""}`).join(", ")}{selected.skipped_rows.length > 6 ? "…" : ""}
                </small>
              )}

              <div style={{ overflowX: "auto", maxHeight: 320 }}>
                <table className="ta-table" style={{ width: "100%", fontSize: 12 }}>
                  <thead><tr><th>Dòng</th>{selected.columns.map((column) => <th key={column.index} style={column.kind === "NUMBER" ? num : undefined}>{column.name}</th>)}</tr></thead>
                  <tbody>
                    {selected.rows.map((row) => (
                      <tr key={String(row[0])}>
                        {row.map((cell, index) => (
                          <td key={index} style={index > 0 && selected.columns[index - 1]?.kind === "NUMBER" ? num : undefined}>
                            {cell === null ? "" : index > 0 && selected.columns[index - 1]?.kind === "NUMBER" ? Number(cell).toLocaleString("vi-VN") : String(cell)}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {selected.row_count > selected.rows.length && <small style={{ color: "var(--text-muted)" }}>Đang xem {selected.rows.length} / {selected.row_count.toLocaleString("vi-VN")} dòng.</small>}
            </section>

            <section className="ta-card" style={{ padding: 16, display: "grid", gap: 12 }}>
              <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                <strong style={{ marginRight: 8 }}>Tính</strong>
                <select className="ta-input" style={{ width: 150 }} value={operation} onChange={(event) => setOperation(event.target.value)}>
                  {OPERATIONS.map(([value, text]) => <option key={value} value={value}>{text}</option>)}
                </select>
                {operation !== "count" && (
                  <label style={label}>cột
                    <select className="ta-input" value={valueColumn} onChange={(event) => setValueColumn(event.target.value)}>
                      {numberColumns.length === 0 && <option value="">(không có cột số)</option>}
                      {numberColumns.map((column) => <option key={column.index}>{column.name}</option>)}
                    </select>
                  </label>
                )}
                <label style={label}>theo
                  <select className="ta-input" value={groupBy} onChange={(event) => setGroupBy(event.target.value)}>
                    <option value="">(không nhóm)</option>
                    {selected.columns.map((column) => <option key={column.index}>{column.name}</option>)}
                  </select>
                </label>
                {groupIsDate && (
                  <select className="ta-input" style={{ width: 110 }} value={period} onChange={(event) => setPeriod(event.target.value)} aria-label="Gom theo">
                    <option value="day">ngày</option><option value="month">tháng</option><option value="quarter">quý</option><option value="year">năm</option>
                  </select>
                )}
                <button className="ta-btn ta-btn-ghost" onClick={() => setFilters([...filters, { column: selected.columns[0]?.name ?? "", op: "eq", value: "" }])} disabled={filters.length >= 5}>
                  <Plus size={15} /> Lọc
                </button>
                <button className="ta-btn ta-btn-primary" onClick={() => void analyse()} disabled={busy || (operation !== "count" && !valueColumn)}>
                  <Calculator size={15} /> Tính
                </button>
              </div>
              {filters.map((filter, position) => (
                <div key={position} style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                  <small style={{ color: "var(--text-muted)" }}>chỉ lấy dòng có</small>
                  <select className="ta-input" value={filter.column} onChange={(event) => setFilters(filters.map((item, i) => i === position ? { ...item, column: event.target.value } : item))}>
                    {selected.columns.map((column) => <option key={column.index}>{column.name}</option>)}
                  </select>
                  <select className="ta-input" style={{ width: 110 }} value={filter.op} onChange={(event) => setFilters(filters.map((item, i) => i === position ? { ...item, op: event.target.value as Filter["op"] } : item))}>
                    {(Object.keys(OPS) as Filter["op"][]).map((op) => <option key={op} value={op}>{OPS[op]}</option>)}
                  </select>
                  <input className="ta-input" style={{ width: 180 }} value={filter.value} placeholder={columnKind(filter.column) === "DATE" ? "dd/mm/yyyy" : "giá trị"}
                    onChange={(event) => setFilters(filters.map((item, i) => i === position ? { ...item, value: event.target.value } : item))} />
                  <button className="ta-btn ta-btn-ghost" aria-label="Bỏ điều kiện" onClick={() => setFilters(filters.filter((_, i) => i !== position))}><X size={14} /></button>
                </div>
              ))}

              {result && (
                <div style={{ display: "grid", gap: 12 }}>
                  <p style={{ margin: 0, fontSize: 14 }}>
                    Kết quả: <b>{asValue(result.total)}</b> trên {result.rows_used.toLocaleString("vi-VN")} dòng
                    {result.rows_left_out_as_totals > 0 && <span style={{ color: "var(--text-muted)" }}> (đã bỏ {result.rows_left_out_as_totals} dòng tổng)</span>}.
                  </p>
                  {result.charts.map((chart) => <FinanceChart key={chart.title} spec={chart} />)}
                  {result.groups.length > 0 && (
                    <div style={{ overflowX: "auto", maxHeight: 360 }}>
                      <table className="ta-table" style={{ width: "100%", fontSize: 13 }}>
                        <thead><tr><th>{result.group_by}</th><th style={num}>Kết quả</th><th style={num}>Số dòng</th><th style={num}>Tỉ trọng</th></tr></thead>
                        <tbody>
                          {result.groups.map((group) => (
                            <tr key={group.group}>
                              <td>{group.group}</td><td style={num}>{asValue(group.value)}</td><td style={num}>{group.rows.toLocaleString("vi-VN")}</td>
                              <td style={num}>{group.share_percent ? `${Number(group.share_percent).toLocaleString("vi-VN")}%` : ""}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}
            </section>
          </>
        )}
      </div>
    </div>
  );
}

const num: React.CSSProperties = { textAlign: "right", whiteSpace: "nowrap" };
const label: React.CSSProperties = { display: "flex", gap: 6, alignItems: "center", fontSize: 13, color: "var(--text-muted)" };
