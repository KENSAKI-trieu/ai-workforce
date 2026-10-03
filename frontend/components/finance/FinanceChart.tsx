"use client";

import { useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatVnd } from "@/components/finance/format";

/**
 * A chart the backend laid out from a Finance report (app/domains/finance/visuals.py).
 * Every number comes from the spec; the only figures worked out here are the positions
 * of a waterfall's floating bars and a donut's shares, both for drawing.
 */
export type ChartKind = "bar" | "stacked_bar" | "line" | "area" | "pie" | "waterfall" | "table";

export interface ChartSeries { key: string; name: string; values: string[]; role?: "reference" }

export interface ChartSpec {
  type: "FINANCE_CHART";
  title: string;
  subtitle?: string | null;
  chart: ChartKind;
  alternatives: ChartKind[];
  categories: string[];
  series: ChartSeries[];
  parts?: { name: string; value: string }[] | null;
  ordinal?: boolean;
}

const KIND_LABELS: Record<ChartKind, string> = {
  bar: "Cột",
  stacked_bar: "Cột chồng",
  line: "Đường",
  area: "Vùng",
  pie: "Tròn",
  waterfall: "Thác nước",
  table: "Bảng",
};

// Categorical slots in fixed order (validated for colour-vision deficiency, adjacent pairs).
const CATEGORICAL = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"];
// What a series is read against (a budget) recedes; the series that matters keeps the colour.
const REFERENCE = "#b4b2a9";
// Aging is an ordered scale: one hue, darker the longer overdue; not yet due is neutral.
const ORDINAL: Record<string, string> = {
  NOT_DUE: "#b4b2a9",
  "1_30": "#86b6ef",
  "31_60": "#3987e5",
  "61_90": "#256abf",
  OVER_90: "#104281",
};
const WATERFALL = { total: "#52514e", up: "#2a78d6", down: "#e34948" };
const AXIS = { fontSize: 11, fill: "#64748B" };
const GRID = "#E2E8F0";

function compactVnd(value: number): string {
  const size = Math.abs(value);
  const short = (amount: number, digits: number) => amount.toLocaleString("vi-VN", { maximumFractionDigits: digits });
  if (size >= 1e9) return `${short(value / 1e9, 2)} tỷ`;
  if (size >= 1e6) return `${short(value / 1e6, 1)} tr`;
  if (size >= 1e3) return `${short(value / 1e3, 0)} nghìn`;
  return short(value, 0);
}

function seriesColors(spec: ChartSpec): string[] {
  let slot = 0;
  return spec.series.map((series) => {
    if (series.role === "reference") return REFERENCE;
    if (spec.ordinal && ORDINAL[series.key]) return ORDINAL[series.key];
    return CATEGORICAL[slot++ % CATEGORICAL.length];
  });
}

function money(value: unknown): string {
  return formatVnd(Number(value));
}

/** `width` is for containers that size to their content (a chat bubble), where 100% is nothing. */
export default function FinanceChart({ spec, height = 280, width }: { spec: ChartSpec; height?: number; width?: number }) {
  const [kind, setKind] = useState<ChartKind>(spec.chart);
  const colors = useMemo(() => seriesColors(spec), [spec]);
  const rows = useMemo(() => spec.categories.map((category, index) => ({
    category,
    ...Object.fromEntries(spec.series.map((series) => [series.key, Number(series.values[index] ?? 0)])),
  })), [spec]);
  const options = spec.alternatives.includes("table") ? spec.alternatives : [...spec.alternatives, "table" as ChartKind];
  const horizontal = spec.categories.length > 6 || spec.categories.some((label) => label.length > 24);
  // A stack's colours carry meaning even with one series (what stage the money is at).
  const legend = spec.series.length >= 2 || kind === "stacked_bar";

  const cartesian = () => {
    const stacked = kind === "stacked_bar";
    if (kind === "line" || kind === "area") {
      const Chart = kind === "line" ? LineChart : AreaChart;
      return (
        <Chart data={rows} margin={{ top: 8, right: 16, left: 4, bottom: 4 }}>
          <CartesianGrid stroke={GRID} vertical={false} />
          <XAxis dataKey="category" tick={AXIS} tickLine={false} axisLine={{ stroke: GRID }} />
          <YAxis tick={AXIS} tickLine={false} axisLine={false} tickFormatter={compactVnd} width={64} />
          <Tooltip formatter={money} />
          {legend && <Legend wrapperStyle={{ fontSize: 12 }} itemSorter={null} />}
          {spec.series.map((series, index) => kind === "line" ? (
            <Line key={series.key} isAnimationActive={false} type="linear" dataKey={series.key} name={series.name} stroke={colors[index]}
              strokeWidth={2} dot={{ r: 4, strokeWidth: 2, fill: "#fff" }} activeDot={{ r: 5 }} />
          ) : (
            <Area key={series.key} isAnimationActive={false} type="linear" dataKey={series.key} name={series.name} stroke={colors[index]}
              strokeWidth={2} fill={colors[index]} fillOpacity={spec.series.length === 1 ? 0.18 : 0.08} />
          ))}
        </Chart>
      );
    }
    const last = spec.series.length - 1;
    return (
      <BarChart data={rows} layout={horizontal ? "vertical" : "horizontal"} margin={{ top: 8, right: 16, left: 4, bottom: 4 }} barGap={2}>
        <CartesianGrid stroke={GRID} vertical={horizontal} horizontal={!horizontal} />
        {horizontal ? (
          <>
            <XAxis type="number" tick={AXIS} tickLine={false} axisLine={false} tickFormatter={compactVnd} />
            <YAxis type="category" dataKey="category" tick={AXIS} tickLine={false} axisLine={{ stroke: GRID }} width={150} />
          </>
        ) : (
          <>
            <XAxis dataKey="category" tick={AXIS} tickLine={false} axisLine={{ stroke: GRID }} />
            <YAxis tick={AXIS} tickLine={false} axisLine={false} tickFormatter={compactVnd} width={64} />
          </>
        )}
        <Tooltip formatter={money} cursor={{ fill: "rgba(100,116,139,0.08)" }} />
        {legend && <Legend wrapperStyle={{ fontSize: 12 }} itemSorter={null} />}
        {spec.series.map((series, index) => (
          <Bar key={series.key} isAnimationActive={false} dataKey={series.key} name={series.name} fill={colors[index]} maxBarSize={32}
            stackId={stacked ? "stack" : undefined}
            stroke={stacked ? "#fff" : undefined} strokeWidth={stacked ? 1 : 0}
            radius={!stacked || index === last ? (horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0]) : 0} />
        ))}
      </BarChart>
    );
  };

  const waterfall = () => {
    const values = spec.series[0]?.values.map(Number) ?? [];
    let running = 0;
    const steps = spec.categories.map((category, index) => {
      const value = values[index] ?? 0;
      const total = index === 0 || index === values.length - 1;
      const step = total
        ? { category, base: 0, amount: value, signed: value, color: WATERFALL.total }
        : { category, base: value >= 0 ? running : running + value, amount: Math.abs(value), signed: value, color: value >= 0 ? WATERFALL.up : WATERFALL.down };
      running = total && index === 0 ? value : running + (total ? 0 : value);
      return step;
    });
    return (
      <BarChart data={steps} margin={{ top: 8, right: 16, left: 4, bottom: 4 }}>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis dataKey="category" tick={AXIS} tickLine={false} axisLine={{ stroke: GRID }} />
        <YAxis tick={AXIS} tickLine={false} axisLine={false} tickFormatter={compactVnd} width={64} />
        <Tooltip
          cursor={{ fill: "rgba(100,116,139,0.08)" }}
          formatter={(_value, _name, item) => [money((item?.payload as { signed?: number })?.signed), "Số tiền"]}
        />
        <Bar dataKey="base" stackId="step" fill="transparent" legendType="none" tooltipType="none" isAnimationActive={false} />
        <Bar dataKey="amount" stackId="step" isAnimationActive={false} maxBarSize={48} radius={[4, 4, 0, 0]}>
          {steps.map((step) => <Cell key={step.category} fill={step.color} />)}
        </Bar>
      </BarChart>
    );
  };

  const parts = spec.parts?.length ? spec.parts : (spec.series.length === 1
    ? spec.categories.map((name, index) => ({ name, value: spec.series[0].values[index] }))
    : []);
  const partTotal = parts.reduce((sum, part) => sum + Number(part.value), 0);

  const donut = () => (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(160px, 1fr) minmax(180px, 1.2fr)", gap: 12, alignItems: "center" }}>
      <ResponsiveContainer width="100%" height={height - 20}>
        <PieChart>
          <Tooltip formatter={money} />
          <Pie data={parts.map((part) => ({ name: part.name, value: Number(part.value) }))} dataKey="value" nameKey="name"
            innerRadius="55%" outerRadius="88%" paddingAngle={1} stroke="#fff" strokeWidth={2} isAnimationActive={false}>
            {parts.map((part, index) => (
              <Cell key={part.name} fill={spec.ordinal ? Object.values(ORDINAL)[index] ?? CATEGORICAL[index] : CATEGORICAL[index % CATEGORICAL.length]} />
            ))}
          </Pie>
        </PieChart>
      </ResponsiveContainer>
      <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 6, fontSize: 13 }}>
        {parts.map((part, index) => (
          <li key={part.name} style={{ display: "grid", gridTemplateColumns: "12px 1fr auto", gap: 8, alignItems: "baseline" }}>
            <span style={{ width: 10, height: 10, borderRadius: 2, background: spec.ordinal ? Object.values(ORDINAL)[index] ?? CATEGORICAL[index] : CATEGORICAL[index % CATEGORICAL.length] }} />
            <span style={{ color: "var(--text-body)" }}>{part.name}</span>
            <span style={{ color: "var(--text-dark)", whiteSpace: "nowrap" }}>
              {formatVnd(part.value)}
              {partTotal > 0 && <small style={{ color: "var(--text-muted)" }}> · {(Number(part.value) * 100 / partTotal).toLocaleString("vi-VN", { maximumFractionDigits: 1 })}%</small>}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );

  const table = () => (
    <div style={{ overflowX: "auto", maxHeight: height + 40 }}>
      <table className="ta-table" style={{ width: "100%", fontSize: 13 }}>
        <thead>
          <tr><th />{spec.series.map((series) => <th key={series.key} style={num}>{series.name}</th>)}</tr>
        </thead>
        <tbody>
          {spec.categories.map((category, index) => (
            <tr key={`${category}-${index}`}>
              <td>{category}</td>
              {spec.series.map((series) => <td key={series.key} style={num}>{formatVnd(series.values[index])}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );

  let body;
  if (kind === "table") body = table();
  else if (kind === "pie") body = parts.length ? donut() : table();
  else body = (
    <ResponsiveContainer width="100%" height={horizontal && (kind === "bar" || kind === "stacked_bar") ? Math.max(height, spec.categories.length * 34 + 60) : height}>
      {kind === "waterfall" ? waterfall() : cartesian()}
    </ResponsiveContainer>
  );

  return (
    <figure className="ta-card" style={{ margin: 0, padding: 14, display: "grid", gap: 8, width: width ?? "100%", maxWidth: "100%", boxSizing: "border-box" }} aria-label={spec.title}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "flex-start", flexWrap: "wrap" }}>
        <figcaption>
          <strong style={{ fontSize: 14, color: "var(--text-dark)" }}>{spec.title}</strong>
          {spec.subtitle && <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>{spec.subtitle}</div>}
        </figcaption>
        {options.length > 1 && (
          <div role="group" aria-label="Kiểu hiển thị" style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
            {options.map((option) => (
              <button key={option} type="button" onClick={() => setKind(option)}
                className={`ta-btn ${option === kind ? "ta-btn-primary" : "ta-btn-ghost"}`}
                style={{ padding: "3px 10px", fontSize: 12 }} aria-pressed={option === kind}>
                {KIND_LABELS[option]}
              </button>
            ))}
          </div>
        )}
      </div>
      {body}
    </figure>
  );
}

const num: React.CSSProperties = { textAlign: "right", whiteSpace: "nowrap" };
