import type { Metadata } from "next";
import GlobalLanguageTranslator from "@/components/GlobalLanguageTranslator";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI Workforce — 企業向けマルチエージェントプラットフォーム",
  description:
    "人事、法務、IT、財務、営業、ナレッジ業務を AI 従業員で自動化する企業管理プラットフォーム。",
  keywords: ["AI", "Enterprise", "Multi-Agent", "HR AI", "Legal AI", "IT AI"],
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ja" suppressHydrationWarning>
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>
        <GlobalLanguageTranslator />
        {children}
      </body>
    </html>
  );
}
