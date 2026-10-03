"use client";

import { Download, LineChart as TrendIcon, Search } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import api from "@/lib/api";
import FinanceChart, { type ChartSpec } from "@/components/finance/FinanceChart";
import { apiError, downloadBlob, formatSide, formatVnd, shiftPeriod } from "@/components/finance/format";

interface Side { debit: string; credit: string }
interface TrialRow { account: string; account_name?: string | null; opening: Side; period_debit: string; period_credit: string; closing: Side }
interface TrialBalance { period: string; rows: TrialRow[]; totals: Record<string, string>; balanced: boolean }
interface Balance { account: string; account_name?: string | null; opening: Side; period_debit: string; period_credit: string; closing: Side; ledger_rows: number; charts?: ChartSpec[] }
interface Charted { charts: ChartSpec[] }
interface BudgetRow { department: string; account: string; budget: string; actual: string; variance: string; used_percent?: string | null; over_budget: boolean }
interface Budget { rows: BudgetRow[]; totals: { budget: string; actual: string; variance: string }; charts?: ChartSpec[] }
interface Entry {
  id: string; entry_date: string; description: string; status: string; proposed_by: string; confidence: string;
  lines: { line_no: number; account_code: string; debit: string; credit: string }[];
}

const ENTRY_STATUS: Record<string, string> = { PENDING_APPROVAL: "Chờ duyệt", POSTED: "Đã ghi sổ", REJECTED: "Bị từ chối" };

export default function BooksTab({ period, canReadLedger, canSeeEntries, canSeeBudget }: {
  period: string; canReadLedger: boolean; canSeeEntries: boolean; canSeeBudget: boolean;
}) {
  const [trial, setTrial] = useState<TrialBalance | null>(null);
  const [budget, setBudget] = useState<Budget | null>(null);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [account, setAccount] = useState("");
  const [balance, setBalance] = useState<Balance | null>(null);
  const [trendAccount, setTrendAccount] = useState("642");
  const [fromPeriod, setFromPeriod] = useState(() => shiftPeriod(period, -5));
  const [trend, setTrend] = useState<Charted | null>(null);
  const [groupBy, setGroupBy] = useState<"account" | "department">("account");
  const [expenses, setExpenses] = useState<Charted | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const requests: Promise<unknown>[] = [];
      if (canSeeBudget) {
        requests.push(api.get<Budget>("/api/v1/finance/reports/budget", { params: { period } }).then(({ data }) => setBudget(data)));
      }
      if (canReadLedger) {
        requests.push(api.get<TrialBalance>("/api/v1/finance/reports/trial-balance", { params: { period } }).then(({ data }) => setTrial(data)));
      }
      if (canSeeEntries) {
        requests.push(api.get<Entry[]>("/api/v1/finance/journal-entries", { params: { period } }).then(({ data }) => setEntries(data)));
      }
      await Promise.all(requests);
    } catch (reason) {
      setError(apiError(reason).message);
    }
  }, [period, canReadLedger, canSeeEntries, canSeeBudget]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const lookup = async () => {
    if (!account) return;
    try {
      const { data } = await api.get<Balance>("/api/v1/finance/reports/balance", { params: { account, period } });
      setBalance(data);
    } catch (reason) {
      setError(apiError(reason).message);
    }
  };

  const loadTrends = useCallback(async () => {
    if (!canReadLedger) return;
    try {
      const range = { from_period: fromPeriod, to_period: period };
      const [expenseResponse, trendResponse] = await Promise.all([
        api.get<Charted>("/api/v1/finance/reports/expenses", { params: { ...range, group_by: groupBy } }),
        trendAccount
          ? api.get<Charted>("/api/v1/finance/reports/account-trend", { params: { ...range, account: trendAccount } })
          : Promise.resolve(null),
      ]);
      setExpenses(expenseResponse.data);
      setTrend(trendResponse ? trendResponse.data : null);
    } catch (reason) {
      setError(apiError(reason).message);
    }
  }, [canReadLedger, fromPeriod, period, groupBy, trendAccount]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadTrends(), 0);
    return () => window.clearTimeout(timer);
    // The account is applied on demand, not on every keystroke.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canReadLedger, fromPeriod, period, groupBy]);

  const exportJournal = async () => {
    try {
      await downloadBlob(`/api/v1/finance/export/journal.xlsx?period=${period}`, `but-toan-${period}.xlsx`,
        (url) => api.get<Blob>(url, { responseType: "blob" }));
    } catch (reason) {
      setError(apiError(reason).message);
    }
  };

  return (
    <div style={{ display: "grid", gap: 16 }}>
      {error && <div className="ta-card" style={{ padding: 12, color: "#B91C1C" }}>{error}</div>}

      {canReadLedger && (
        <section className="ta-card" style={{ padding: 16 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 10 }}>
            <strong>Số dư tài khoản kỳ {period}</strong>
            <input className="ta-input" style={{ width: 140 }} placeholder="Số TK, vd 331" value={account}
              onChange={(event) => setAccount(event.target.value.replace(/\D/g, ""))}
              onKeyDown={(event) => { if (event.key === "Enter") void lookup(); }} />
            <button className="ta-btn ta-btn-ghost" onClick={() => void lookup()}><Search size={15} /> Tra</button>
          </div>
          {balance && (
            <p style={{ margin: 0, fontSize: 14 }}>
              TK <b>{balance.account}</b> {balance.account_name} · Đầu kỳ {formatSide(balance.opening)} · Phát sinh Nợ {formatVnd(balance.period_debit)},
              Có {formatVnd(balance.period_credit)} · <b>Cuối kỳ {formatSide(balance.closing)}</b> ({balance.ledger_rows} dòng sổ)
            </p>
          )}
          {balance?.charts?.map((chart) => <div key={chart.title} style={{ marginTop: 12 }}><FinanceChart spec={chart} /></div>)}
        </section>
      )}

      {canReadLedger && (
        <section className="ta-card" style={{ padding: 16, display: "grid", gap: 12 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <strong style={{ marginRight: "auto" }}>Xu hướng và cơ cấu chi phí</strong>
            <label style={label}>Từ kỳ
              <input className="ta-input" type="month" style={{ width: 180 }} value={fromPeriod} max={period}
                onChange={(event) => event.target.value && setFromPeriod(event.target.value)} />
            </label>
            <span style={label}>đến {period}</span>
            <label style={label}>Tài khoản
              <input className="ta-input" style={{ width: 90 }} value={trendAccount}
                onChange={(event) => setTrendAccount(event.target.value.replace(/\D/g, ""))}
                onKeyDown={(event) => { if (event.key === "Enter") void loadTrends(); }} />
            </label>
            <button className="ta-btn ta-btn-ghost" onClick={() => void loadTrends()}><TrendIcon size={15} /> Vẽ</button>
            <select className="ta-input" style={{ width: 170 }} value={groupBy} aria-label="Chia chi phí theo"
              onChange={(event) => setGroupBy(event.target.value as "account" | "department")}>
              <option value="account">Chi phí theo tài khoản</option>
              <option value="department">Chi phí theo phòng ban</option>
            </select>
          </div>
          {expenses && (expenses.charts.length
            ? expenses.charts.map((chart) => <FinanceChart key={chart.title} spec={chart} />)
            : <small style={{ color: "var(--text-muted)" }}>Chưa có chi phí (TK 6xx, 8xx) trong khoảng này.</small>)}
          {trend?.charts.map((chart) => <FinanceChart key={chart.title} spec={chart} />)}
        </section>
      )}

      {canReadLedger && trial && (
        <section className="ta-card" style={{ padding: 16 }}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 10 }}>
            <strong>Bảng cân đối số phát sinh {trial.period}</strong>
            <span className={`ta-badge ${trial.balanced ? "ta-badge-success" : "ta-badge-danger"}`}>{trial.balanced ? "Cân" : "Không cân"}</span>
          </div>
          {trial.rows.length === 0 ? <small style={{ color: "var(--text-muted)" }}>Chưa có số liệu sổ cái cho kỳ này.</small> : (
            <div style={{ overflowX: "auto" }}>
              <table className="ta-table" style={{ width: "100%", fontSize: 13 }}>
                <thead><tr><th>TK</th><th>Tên</th><th style={num}>Đầu kỳ</th><th style={num}>PS Nợ</th><th style={num}>PS Có</th><th style={num}>Cuối kỳ</th></tr></thead>
                <tbody>
                  {trial.rows.map((row) => (
                    <tr key={row.account}>
                      <td>{row.account}</td><td>{row.account_name}</td><td style={num}>{formatSide(row.opening)}</td>
                      <td style={num}>{formatVnd(row.period_debit)}</td><td style={num}>{formatVnd(row.period_credit)}</td><td style={num}>{formatSide(row.closing)}</td>
                    </tr>
                  ))}
                  <tr style={{ fontWeight: 700 }}>
                    <td colSpan={3}>Tổng</td><td style={num}>{formatVnd(trial.totals.period_debit)}</td><td style={num}>{formatVnd(trial.totals.period_credit)}</td><td />
                  </tr>
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {budget && (
        <section className="ta-card" style={{ padding: 16 }}>
          <strong style={{ display: "block", marginBottom: 10 }}>Ngân sách so với thực tế {period}</strong>
          {budget.charts?.map((chart) => <div key={chart.title} style={{ marginBottom: 12 }}><FinanceChart spec={chart} /></div>)}
          {budget.rows.length === 0 ? <small style={{ color: "var(--text-muted)" }}>Chưa nhập ngân sách cho kỳ này.</small> : (
            <table className="ta-table" style={{ width: "100%", fontSize: 13 }}>
              <thead><tr><th>Phòng ban</th><th>TK</th><th style={num}>Ngân sách</th><th style={num}>Thực tế</th><th style={num}>Chênh lệch</th><th style={num}>Đã dùng</th></tr></thead>
              <tbody>
                {budget.rows.map((row) => (
                  <tr key={`${row.department}-${row.account}`} style={{ color: row.over_budget ? "#B91C1C" : undefined }}>
                    <td>{row.department}</td><td>{row.account}</td><td style={num}>{formatVnd(row.budget)}</td>
                    <td style={num}>{formatVnd(row.actual)}</td><td style={num}>{formatVnd(row.variance)}</td>
                    <td style={num}>{row.used_percent ? `${Number(row.used_percent).toLocaleString("vi-VN")}%` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}

      {canSeeEntries && (
        <section className="ta-card" style={{ padding: 16 }}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 10 }}>
            <strong>Bút toán kỳ {period}</strong>
            {canReadLedger && <button className="ta-btn ta-btn-ghost" onClick={() => void exportJournal()}><Download size={15} /> Xuất Excel để nhập phần mềm kế toán</button>}
          </div>
          {entries.length === 0 ? <small style={{ color: "var(--text-muted)" }}>Chưa có bút toán.</small> : (
            <table className="ta-table" style={{ width: "100%", fontSize: 13 }}>
              <thead><tr><th>Ngày</th><th>Diễn giải</th><th>Định khoản</th><th>Trạng thái</th></tr></thead>
              <tbody>
                {entries.map((entry) => (
                  <tr key={entry.id}>
                    <td>{entry.entry_date}</td>
                    <td>{entry.description}</td>
                    <td>{entry.lines.map((line) => `${Number(line.debit) ? "Nợ" : "Có"} ${line.account_code} ${formatVnd(Number(line.debit) ? line.debit : line.credit)}`).join("; ")}</td>
                    <td>{ENTRY_STATUS[entry.status] || entry.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}
    </div>
  );
}

const num: React.CSSProperties = { textAlign: "right", whiteSpace: "nowrap" };
const label: React.CSSProperties = { display: "flex", gap: 6, alignItems: "center", fontSize: 13, color: "var(--text-muted)" };
