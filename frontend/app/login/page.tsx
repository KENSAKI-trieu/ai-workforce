"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/store/useAuthStore";
import { Bot, Eye, EyeOff, Loader2, Building2, ArrowRight, CheckCircle2, Sparkles, Shield, Zap } from "lucide-react";
import Link from "next/link";

export default function LoginPage() {
  const router = useRouter();
  const { login } = useAuthStore();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await login(email, password);
      router.push("/dashboard");
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
      let msg: string;
      if (Array.isArray(detail)) {
        msg = detail
          .map((d: { msg?: string; loc?: string[] }) =>
            d.loc ? `${d.loc.slice(-1)[0]}: ${d.msg}` : d.msg ?? String(d)
          )
          .join("; ");
      } else if (typeof detail === "string") {
        msg = detail;
      } else {
        msg = "Đăng nhập thất bại. Kiểm tra lại email và mật khẩu.";
      }
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  const FEATURES = [
    { icon: Sparkles, text: "AI Agents tự động hoá toàn bộ nghiệp vụ" },
    { icon: Shield, text: "Bảo mật doanh nghiệp cấp cao" },
    { icon: Zap, text: "Xử lý 10,000+ hội thoại mỗi ngày" },
    { icon: CheckCircle2, text: "Tích hợp LangGraph + RAG Knowledge Base" },
  ];

  return (
    <main
      style={{
        minHeight: "100vh",
        display: "flex",
        background: "var(--body-bg)",
      }}
    >
      {/* ── Left Panel: Gradient Branding (hidden on mobile) ── */}
      <div
        style={{
          flex: "0 0 45%",
          background: "linear-gradient(145deg, #1C2434 0%, #2D3A52 50%, #3C50E0 100%)",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          padding: "60px 56px",
          position: "relative",
          overflow: "hidden",
        }}
        className="login-panel-left"
      >
        {/* Decorative circles */}
        <div
          style={{
            position: "absolute",
            top: "-80px",
            right: "-80px",
            width: "320px",
            height: "320px",
            borderRadius: "50%",
            background: "rgba(99,102,241,0.15)",
            pointerEvents: "none",
          }}
        />
        <div
          style={{
            position: "absolute",
            bottom: "-120px",
            left: "-60px",
            width: "400px",
            height: "400px",
            borderRadius: "50%",
            background: "rgba(60,80,224,0.10)",
            pointerEvents: "none",
          }}
        />
        <div
          style={{
            position: "absolute",
            top: "40%",
            right: "10%",
            width: "160px",
            height: "160px",
            borderRadius: "50%",
            background: "rgba(139,92,246,0.10)",
            pointerEvents: "none",
          }}
        />

        {/* Logo */}
        <div style={{ display: "flex", alignItems: "center", gap: "14px", marginBottom: "56px", position: "relative", zIndex: 1 }}>
          <div
            style={{
              width: "48px",
              height: "48px",
              borderRadius: "14px",
              background: "linear-gradient(135deg, #6366f1, #8b5cf6)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              boxShadow: "0 8px 24px rgba(99,102,241,0.40)",
            }}
          >
            <Bot size={26} color="white" />
          </div>
          <div>
            <div style={{ fontSize: "1.1rem", fontWeight: 800, color: "#FFFFFF", letterSpacing: "-0.01em" }}>
              AI Workforce
            </div>
            <div style={{ fontSize: "0.72rem", color: "rgba(255,255,255,0.55)", marginTop: "1px" }}>
              Enterprise Platform
            </div>
          </div>
        </div>

        {/* Heading */}
        <div style={{ position: "relative", zIndex: 1, marginBottom: "40px" }}>
          <h1
            style={{
              fontSize: "2rem",
              fontWeight: 800,
              color: "#FFFFFF",
              lineHeight: 1.2,
              letterSpacing: "-0.02em",
              marginBottom: "16px",
            }}
          >
            Nền tảng AI Agents
            <br />
            <span
              style={{
                background: "linear-gradient(90deg, #818CF8, #C084FC)",
                WebkitBackgroundClip: "text",
                WebkitTextFillColor: "transparent",
                backgroundClip: "text",
              }}
            >
              dành cho Doanh nghiệp
            </span>
          </h1>
          <p style={{ fontSize: "0.9rem", color: "rgba(255,255,255,0.65)", lineHeight: 1.6 }}>
            Tự động hoá HR, Pháp lý, Tài chính, Marketing & Knowledge với AI Employees thông minh.
          </p>
        </div>

        {/* Feature list */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: "14px",
            position: "relative",
            zIndex: 1,
          }}
        >
          {FEATURES.map(({ icon: Icon, text }) => (
            <div
              key={text}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "12px",
              }}
            >
              <div
                style={{
                  width: "34px",
                  height: "34px",
                  borderRadius: "8px",
                  background: "rgba(255,255,255,0.10)",
                  border: "1px solid rgba(255,255,255,0.12)",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  flexShrink: 0,
                }}
              >
                <Icon size={16} color="rgba(255,255,255,0.85)" />
              </div>
              <span style={{ fontSize: "0.875rem", color: "rgba(255,255,255,0.80)" }}>{text}</span>
            </div>
          ))}
        </div>

        {/* Bottom tag */}
        <div
          style={{
            marginTop: "auto",
            paddingTop: "48px",
            fontSize: "0.75rem",
            color: "rgba(255,255,255,0.35)",
            position: "relative",
            zIndex: 1,
          }}
        >
          © 2026 AI Workforce Enterprise · Powered by LangGraph + FastAPI
        </div>
      </div>

      {/* ── Right Panel: Login Form ── */}
      <div
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          padding: "40px 24px",
          background: "#FFFFFF",
        }}
      >
        <div
          className="fade-in-up"
          style={{ width: "100%", maxWidth: "400px" }}
        >
          {/* Mobile logo (only shows when left panel is hidden) */}
          <div
            style={{
              display: "none",
              alignItems: "center",
              gap: "10px",
              marginBottom: "32px",
            }}
            className="login-mobile-logo"
          >
            <div
              style={{
                width: "38px",
                height: "38px",
                borderRadius: "10px",
                background: "linear-gradient(135deg, #6366f1, #8b5cf6)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <Bot size={20} color="white" />
            </div>
            <span style={{ fontSize: "1rem", fontWeight: 700, color: "var(--text-dark)" }}>
              AI Workforce
            </span>
          </div>

          {/* Title */}
          <div style={{ marginBottom: "32px" }}>
            <h2
              style={{
                fontSize: "1.6rem",
                fontWeight: 800,
                color: "var(--text-dark)",
                letterSpacing: "-0.02em",
                marginBottom: "8px",
              }}
            >
              Chào mừng trở lại!
            </h2>
            <p style={{ fontSize: "0.875rem", color: "var(--text-muted)" }}>
              Nhập thông tin đăng nhập để truy cập hệ thống.
            </p>
          </div>

          {/* Form */}
          <form
            onSubmit={handleSubmit}
            style={{ display: "flex", flexDirection: "column", gap: "18px" }}
          >
            {/* Email */}
            <div style={{ display: "flex", flexDirection: "column", gap: "7px" }}>
              <label
                htmlFor="login-email"
                style={{
                  fontSize: "0.82rem",
                  fontWeight: 600,
                  color: "var(--text-dark)",
                }}
              >
                Email
              </label>
              <input
                id="login-email"
                type="email"
                className="ta-input"
                placeholder="your@company.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoComplete="email"
              />
            </div>

            {/* Password */}
            <div style={{ display: "flex", flexDirection: "column", gap: "7px" }}>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                }}
              >
                <label
                  htmlFor="login-password"
                  style={{ fontSize: "0.82rem", fontWeight: 600, color: "var(--text-dark)" }}
                >
                  Mật khẩu
                </label>
                <button
                  type="button"
                  style={{
                    fontSize: "0.78rem",
                    color: "var(--primary)",
                    background: "none",
                    border: "none",
                    cursor: "pointer",
                    fontWeight: 500,
                  }}
                >
                  Quên mật khẩu?
                </button>
              </div>

              <div style={{ position: "relative" }}>
                <input
                  id="login-password"
                  type={showPassword ? "text" : "password"}
                  className="ta-input"
                  placeholder="••••••••"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  autoComplete="current-password"
                  style={{ paddingRight: "46px" }}
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  style={{
                    position: "absolute",
                    right: "14px",
                    top: "50%",
                    transform: "translateY(-50%)",
                    background: "none",
                    border: "none",
                    cursor: "pointer",
                    color: "var(--text-light)",
                    display: "flex",
                    alignItems: "center",
                    padding: "2px",
                  }}
                >
                  {showPassword ? <EyeOff size={17} /> : <Eye size={17} />}
                </button>
              </div>
            </div>

            {/* Error */}
            {error && (
              <div
                style={{
                  background: "#FFF5F5",
                  border: "1px solid #FECACA",
                  borderRadius: "8px",
                  padding: "11px 14px",
                  fontSize: "0.85rem",
                  color: "#DC2626",
                  display: "flex",
                  alignItems: "flex-start",
                  gap: "8px",
                }}
              >
                <span style={{ fontSize: "1rem", flexShrink: 0 }}>⚠️</span>
                {error}
              </div>
            )}

            {/* Submit */}
            <button
              id="login-submit-btn"
              type="submit"
              className="ta-btn ta-btn-primary"
              disabled={loading}
              style={{ width: "100%", height: "46px", fontSize: "0.95rem", marginTop: "4px" }}
            >
              {loading ? (
                <>
                  <Loader2 size={18} style={{ animation: "spin 1s linear infinite" }} />
                  Đang đăng nhập...
                </>
              ) : (
                <>
                  Đăng nhập
                  <ArrowRight size={18} />
                </>
              )}
            </button>
          </form>

          {/* Divider */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "12px",
              margin: "24px 0",
            }}
          >
            <div style={{ flex: 1, height: "1px", background: "var(--border)" }} />
            <span style={{ fontSize: "0.78rem", color: "var(--text-light)" }}>hoặc</span>
            <div style={{ flex: 1, height: "1px", background: "var(--border)" }} />
          </div>

          {/* Register link */}
          <Link
            href="/register"
            id="go-to-register-link"
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: "8px",
              width: "100%",
              padding: "12px",
              borderRadius: "8px",
              border: "1.5px solid var(--border)",
              background: "transparent",
              color: "var(--text-muted)",
              fontSize: "0.875rem",
              fontWeight: 500,
              textDecoration: "none",
              transition: "all 0.2s ease",
            }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLAnchorElement).style.borderColor = "var(--primary)";
              (e.currentTarget as HTMLAnchorElement).style.color = "var(--primary)";
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLAnchorElement).style.borderColor = "var(--border)";
              (e.currentTarget as HTMLAnchorElement).style.color = "var(--text-muted)";
            }}
          >
            <Building2 size={17} />
            Tạo tổ chức mới (Register)
          </Link>

          {/* Footer */}
          <p
            style={{
              textAlign: "center",
              marginTop: "28px",
              fontSize: "0.75rem",
              color: "var(--text-light)",
            }}
          >
            Bằng cách đăng nhập, bạn đồng ý với{" "}
            <span style={{ color: "var(--primary)", cursor: "pointer" }}>Điều khoản sử dụng</span>
            {" "}và{" "}
            <span style={{ color: "var(--primary)", cursor: "pointer" }}>Chính sách bảo mật</span>.
          </p>
        </div>
      </div>

      <style>{`
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
        @media (max-width: 768px) {
          .login-panel-left { display: none !important; }
          .login-mobile-logo { display: flex !important; }
        }
      `}</style>
    </main>
  );
}
