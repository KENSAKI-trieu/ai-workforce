"use client";

import Link from "next/link";
import { ArrowLeft, Check } from "lucide-react";

import styles from "../knowledge.module.css";

const steps = ["Chọn nguồn dữ liệu", "Cấu hình phân đoạn", "Xử lý và hoàn tất"];

export default function WizardHeader({ current }: { current: 1 | 2 | 3 }) {
  return (
    <header className={styles.wizardTopbar}>
      <Link className={styles.backLink} href={current === 1 ? "/knowledge" : current === 2 ? "/knowledge/new" : "/knowledge/chunking"}>
        <ArrowLeft size={17} /> Kiến thức
      </Link>
      <nav className={styles.steps} aria-label="Các bước tạo kiến thức">
        {steps.map((label, index) => {
          const step = index + 1;
          const className = `${styles.step} ${step === current ? styles.stepActive : step < current ? styles.stepDone : ""}`;
          return (
            <div key={label} style={{ display: "contents" }}>
              {index > 0 && <span className={styles.stepLine} />}
              <span className={className}>
                <span className={styles.stepNumber}>{step < current ? <Check size={13} /> : step}</span>
                <span>{label}</span>
              </span>
            </div>
          );
        })}
      </nav>
      <span />
    </header>
  );
}
