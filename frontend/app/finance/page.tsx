"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { MessageSquare, Receipt } from "lucide-react";
import Sidebar from "@/components/Sidebar";
import BooksTab from "@/components/finance/BooksTab";
import DataTab from "@/components/finance/DataTab";
import DebtsTab from "@/components/finance/DebtsTab";
import InvoicesTab from "@/components/finance/InvoicesTab";
import { currentPeriod } from "@/components/finance/format";
import { useAuthStore, userCan } from "@/store/useAuthStore";

type Tab = "invoices" | "books" | "debts" | "data";

/**
 * The books the Finance agent reads, for the people who keep them.
 *
 * Every tab shows only to positions holding its box in org-structure; the server checks
 * each call again. Nothing here posts or pays on its own: drafts go to the approvals center.
 */
export default function FinancePage() {
  const router = useRouter();
  const { isAuthenticated, hasHydrated, user } = useAuthStore();
  const can = (code: string) => userCan(user, code);
  const tabs = useMemo(() => {
    const visible: { key: Tab; label: string }[] = [];
    if (userCan(user, "finance.invoice.process") || userCan(user, "finance.ledger.view") || userCan(user, "finance.ar_ap.view")) {
      visible.push({ key: "invoices", label: "Hoá đơn" });
    }
    if (userCan(user, "finance.ledger.view") || userCan(user, "finance.budget.view_own") || userCan(user, "finance.journal.draft")) {
      visible.push({ key: "books", label: "Sổ sách & báo cáo" });
    }
    if (userCan(user, "finance.ar_ap.view")) visible.push({ key: "debts", label: "Công nợ" });
    if (userCan(user, "finance.import.manage")) visible.push({ key: "data", label: "Dữ liệu & cài đặt" });
    return visible;
  }, [user]);
  const [chosen, setChosen] = useState<Tab | null>(null);
  const [period, setPeriod] = useState(currentPeriod());
  const tab = chosen && tabs.some((item) => item.key === chosen) ? chosen : tabs[0]?.key;

  useEffect(() => {
    if (hasHydrated && !isAuthenticated) router.replace("/login");
  }, [hasHydrated, isAuthenticated, router]);

  if (!hasHydrated || !isAuthenticated) return null;

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "var(--body-bg)" }}>
      <Sidebar />
      <div style={{ flex: 1, minWidth: 0 }}>
        <header className="ta-topbar">
          <div className="breadcrumb">
            <span>Home</span><span className="breadcrumb-sep">›</span>
            <span className="breadcrumb-current">Sổ sách Tài chính</span>
          </div>
          <button className="ta-btn ta-btn-ghost" onClick={() => router.push("/agents/FINANCE")}>
            <MessageSquare size={15} /> Hỏi Trợ lý Tài chính
          </button>
        </header>
        <main style={{ padding: "24px 32px" }}>
          <h1 style={{ display: "flex", alignItems: "center", gap: 10, fontSize: "1.5rem", fontWeight: 800 }}>
            <Receipt size={24} color="var(--primary)" /> Sổ sách Tài chính
          </h1>
          <p style={{ color: "var(--text-muted)", margin: "6px 0 18px" }}>
            Hoá đơn, sổ cái, công nợ mà Trợ lý Tài chính đọc. Bút toán, phiếu chi và thư nhắc nợ đều là bản nháp, chỉ có hiệu lực khi được duyệt.
          </p>
          {tabs.length === 0 ? (
            <div className="ta-card" style={{ padding: 24 }}>Chức vụ của bạn chưa được cấp quyền Tài chính nào.</div>
          ) : (
            <>
              <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 16, flexWrap: "wrap" }}>
                {tabs.map((item) => (
                  <button key={item.key} className={`ta-btn ${tab === item.key ? "ta-btn-primary" : "ta-btn-ghost"}`} onClick={() => setChosen(item.key)}>
                    {item.label}
                  </button>
                ))}
                {tab === "books" && (
                  <label style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 6, fontSize: 13 }}>
                    Kỳ
                    <input type="month" className="ta-input" style={{ width: 160 }} value={period} onChange={(event) => setPeriod(event.target.value || currentPeriod())} />
                  </label>
                )}
              </div>
              {tab === "invoices" && <InvoicesTab canUpload={can("finance.invoice.process")} canPropose={can("finance.journal.draft")} />}
              {tab === "books" && (
                <BooksTab
                  period={period}
                  canReadLedger={can("finance.ledger.view")}
                  canSeeEntries={can("finance.journal.draft") || can("finance.ledger.view")}
                  canSeeBudget={can("finance.ledger.view") || can("finance.budget.view_own")}
                />
              )}
              {tab === "debts" && <DebtsTab canDraft={can("finance.journal.draft")} canRemind={can("finance.reminder.send")} />}
              {tab === "data" && <DataTab />}
            </>
          )}
        </main>
      </div>
    </div>
  );
}
