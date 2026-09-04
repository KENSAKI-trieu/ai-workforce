"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { ReactNode, useEffect } from "react";
import { Bell, ChevronRight, Home } from "lucide-react";

import Sidebar from "@/components/Sidebar";
import { useAuthStore } from "@/store/useAuthStore";

interface AdminShellProps {
  title: string;
  description: string;
  children: ReactNode;
  action?: ReactNode;
}

export default function AdminShell({
  title,
  description,
  children,
  action,
}: AdminShellProps) {
  const router = useRouter();
  const { isAuthenticated, hasHydrated, user } = useAuthStore();

  useEffect(() => {
    const hasToken =
      typeof window !== "undefined" &&
      Boolean(localStorage.getItem("access_token"));
    if (hasHydrated && !isAuthenticated && !hasToken) {
      router.replace("/login");
    }
  }, [hasHydrated, isAuthenticated, router]);

  if (!hasHydrated) {
    return <div className="ops-loading">Đang tải phiên làm việc…</div>;
  }

  return (
    <div className="ops-shell">
      <Sidebar />
      <main className="ops-main">
        <header className="ops-topbar">
          <div>
            <div className="breadcrumb">
              <Home size={14} />
              <Link href="/dashboard">Trang chủ</Link>
              <ChevronRight size={13} />
              <span className="breadcrumb-current">{title}</span>
            </div>
            <h1>{title}</h1>
            <p>{description}</p>
          </div>
          <div className="ops-topbar-actions">
            {action}
            <Link
              className="ops-icon-button"
              href="/notifications"
              aria-label="Mở thông báo"
            >
              <Bell size={19} />
            </Link>
            <div className="ops-user-chip">
              <span>{user?.full_name?.charAt(0).toUpperCase() || "U"}</span>
              <div>
                <strong>{user?.full_name || "Người dùng"}</strong>
                <small>{user?.position_name || user?.role || ""}</small>
              </div>
            </div>
          </div>
        </header>
        <section className="ops-content">{children}</section>
      </main>
    </div>
  );
}
