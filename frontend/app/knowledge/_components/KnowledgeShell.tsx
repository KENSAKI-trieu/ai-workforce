"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import { useAuthStore } from "@/store/useAuthStore";
import styles from "../knowledge.module.css";

export default function KnowledgeShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { hasHydrated, isAuthenticated } = useAuthStore();

  useEffect(() => {
    if (hasHydrated && !isAuthenticated) router.replace("/login");
  }, [hasHydrated, isAuthenticated, router]);

  if (!hasHydrated || !isAuthenticated) return null;

  return (
    <div className={styles.page}>
      <Sidebar />
      <div className={styles.content}>{children}</div>
    </div>
  );
}
