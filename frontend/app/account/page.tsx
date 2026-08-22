"use client";

import { ChangeEvent, FormEvent, useEffect, useState } from "react";
import { Camera, Heart, MapPin, Save, ShieldCheck, UserRound, Wallet } from "lucide-react";
import Image from "next/image";
import { useRouter } from "next/navigation";

import AvatarCropper from "@/components/AvatarCropper";
import Sidebar from "@/components/Sidebar";
import api from "@/lib/api";
import { useAuthStore } from "@/store/useAuthStore";

interface FullProfile {
  id: string;
  email: string;
  full_name: string;
  role: string;
  department: string;
  avatar_url: string | null;
  phone: string | null;
  address: string | null;
  city: string | null;
  country: string | null;
  date_of_birth: string | null;
  gender: string | null;
  bio: string | null;
  emergency_contact_name: string | null;
  emergency_contact_phone: string | null;
  preferences: {
    hobbies?: string[];
    preferred_language?: string;
    timezone?: string;
    work_style?: string | null;
    theme?: "light" | "dark" | "system";
    communication_channels?: string[];
  };
  employment: {
    job_title: string | null;
    employee_code: string | null;
    hire_date: string | null;
    monthly_salary: number | null;
    salary_currency: string;
    leave: { total_days: number; used_days: number; remaining_days: number };
  };
}

interface ProfileForm {
  full_name: string;
  phone: string;
  address: string;
  city: string;
  country: string;
  date_of_birth: string;
  gender: string;
  bio: string;
  emergency_contact_name: string;
  emergency_contact_phone: string;
  hobbies: string;
  preferred_language: string;
  timezone: string;
  work_style: string;
  theme: "light" | "dark" | "system";
}

const EMPTY_FORM: ProfileForm = {
  full_name: "", phone: "", address: "", city: "", country: "Vietnam",
  date_of_birth: "", gender: "prefer_not_to_say", bio: "",
  emergency_contact_name: "", emergency_contact_phone: "", hobbies: "",
  preferred_language: "vi", timezone: "Asia/Ho_Chi_Minh", work_style: "", theme: "system",
};

function toForm(profile: FullProfile): ProfileForm {
  return {
    full_name: profile.full_name || "",
    phone: profile.phone || "",
    address: profile.address || "",
    city: profile.city || "",
    country: profile.country || "Vietnam",
    date_of_birth: profile.date_of_birth || "",
    gender: profile.gender || "prefer_not_to_say",
    bio: profile.bio || "",
    emergency_contact_name: profile.emergency_contact_name || "",
    emergency_contact_phone: profile.emergency_contact_phone || "",
    hobbies: (profile.preferences?.hobbies || []).join(", "),
    preferred_language: profile.preferences?.preferred_language || "vi",
    timezone: profile.preferences?.timezone || "Asia/Ho_Chi_Minh",
    work_style: profile.preferences?.work_style || "",
    theme: profile.preferences?.theme || "system",
  };
}

export default function AccountPage() {
  const router = useRouter();
  const { isAuthenticated, hasHydrated, fetchMe } = useAuthStore();
  const [profile, setProfile] = useState<FullProfile | null>(null);
  const [form, setForm] = useState<ProfileForm>(EMPTY_FORM);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [cropFile, setCropFile] = useState<File | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!hasHydrated) return;
    if (!isAuthenticated) {
      router.replace("/login");
      return;
    }
    let active = true;
    void api.get<FullProfile>("/api/v1/users/me/profile")
      .then(({ data }) => {
        if (!active) return;
        setProfile(data);
        setForm(toForm(data));
      })
      .catch(() => active && setError("Không thể tải thông tin tài khoản."))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [hasHydrated, isAuthenticated, router]);

  function updateField<K extends keyof ProfileForm>(key: K, value: ProfileForm[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function saveProfile(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setMessage("");
    setError("");
    try {
      const { data } = await api.patch<FullProfile>("/api/v1/users/me/profile", {
        full_name: form.full_name,
        phone: form.phone || null,
        address: form.address || null,
        city: form.city || null,
        country: form.country || null,
        date_of_birth: form.date_of_birth || null,
        gender: form.gender || null,
        bio: form.bio || null,
        emergency_contact_name: form.emergency_contact_name || null,
        emergency_contact_phone: form.emergency_contact_phone || null,
        preferences: {
          hobbies: form.hobbies.split(",").map((item) => item.trim()).filter(Boolean),
          preferred_language: form.preferred_language,
          timezone: form.timezone,
          work_style: form.work_style || null,
          theme: form.theme,
          communication_channels: profile?.preferences?.communication_channels || ["IN_APP"],
        },
      });
      setProfile(data);
      setForm(toForm(data));
      await fetchMe();
      setMessage("Đã cập nhật thông tin tài khoản.");
    } catch {
      setError("Không thể lưu hồ sơ. Vui lòng kiểm tra lại dữ liệu.");
    } finally {
      setSaving(false);
    }
  }

  function selectAvatar(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setMessage("");
    setError("");

    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type) || file.size > 5 * 1024 * 1024) {
      setError("Chỉ hỗ trợ JPEG, PNG, WebP tối đa 5 MB.");
      event.target.value = "";
      return;
    }

    setCropFile(file);
    event.target.value = "";
  }

  async function uploadAvatar(blob: Blob) {
    setUploading(true);
    setMessage("");
    setError("");
    const payload = new FormData();
    payload.append("file", blob, "avatar.webp");
    try {
      const { data } = await api.post<{ avatar_url: string }>("/api/v1/users/me/avatar", payload);
      setProfile((current) => current ? { ...current, avatar_url: data.avatar_url } : current);
      await fetchMe();
      setCropFile(null);
      setMessage("Đã cập nhật ảnh đại diện.");
    } catch {
      setError("Không thể tải ảnh đã chỉnh sửa. Vui lòng kiểm tra kết nối và thử lại.");
    } finally {
      setUploading(false);
    }
  }

  if (!hasHydrated || !isAuthenticated) return null;

  const salary = profile?.employment.monthly_salary;
  const leave = profile?.employment.leave;

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "var(--body-bg)" }}>
      <Sidebar/>
      <main style={{ flex: 1, minWidth: 0, padding: "28px 34px", overflowY: "auto" }}>
        <div style={{ maxWidth: 1120, margin: "0 auto" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 22 }}>
            <div>
              <h1 style={{ fontSize: 24, fontWeight: 800, color: "var(--text-dark)" }}>Thông tin tài khoản</h1>
              <p style={{ marginTop: 5, color: "var(--text-muted)" }}>Quản lý hồ sơ cá nhân, sở thích và xem thông tin nhân sự của bạn.</p>
            </div>
            <span className="ta-badge ta-badge-success"><ShieldCheck size={13}/> Hồ sơ được bảo vệ</span>
          </div>

          {message && <div className="ta-card" style={{ padding: 12, color: "#047857", background: "#ECFDF5", marginBottom: 14 }}>{message}</div>}
          {error && <div className="ta-card" style={{ padding: 12, color: "#B91C1C", background: "#FEF2F2", marginBottom: 14 }}>{error}</div>}
          {loading || !profile ? <div className="ta-card" style={{ padding: 30 }}>Đang tải hồ sơ...</div> : (
            <>
              <section className="ta-card" style={{ padding: 22, marginBottom: 18, display: "flex", alignItems: "center", gap: 18 }}>
                {profile.avatar_url ? (
                  <Image src={profile.avatar_url} alt={profile.full_name} width={82} height={82} unoptimized style={{ width: 82, height: 82, borderRadius: "50%", objectFit: "cover", border: "3px solid #E0E7FF" }}/>
                ) : (
                  <div style={{ width: 82, height: 82, borderRadius: "50%", display: "grid", placeItems: "center", background: "linear-gradient(135deg,#3C50E0,#8B5CF6)", color: "#fff", fontSize: 28, fontWeight: 800 }}>{profile.full_name.charAt(0).toUpperCase()}</div>
                )}
                <div style={{ flex: 1 }}>
                  <h2 style={{ fontSize: 19, fontWeight: 800 }}>{profile.full_name}</h2>
                  <div style={{ color: "var(--text-muted)", marginTop: 4 }}>{profile.email}</div>
                  <div style={{ display: "flex", gap: 7, marginTop: 8 }}><span className="ta-badge ta-badge-info">{profile.role}</span><span className="ta-badge">{profile.department}</span></div>
                </div>
                <label className="ta-btn ta-btn-primary" style={{ cursor: uploading ? "wait" : "pointer" }}>
                  <Camera size={15}/> {uploading ? "Đang tải..." : "Chọn & chỉnh ảnh"}
                  <input type="file" accept="image/jpeg,image/png,image/webp" onChange={selectAvatar} disabled={uploading} hidden/>
                </label>
              </section>

              <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1.5fr) minmax(300px,.7fr)", gap: 18, alignItems: "start" }}>
                <form onSubmit={saveProfile} className="ta-card" style={{ padding: 22 }}>
                  <h2 style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 16, fontWeight: 800, marginBottom: 17 }}><UserRound size={18} color="#4F46E5"/> Hồ sơ cá nhân</h2>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 13 }}>
                    <Field label="Họ và tên"><input className="ta-input" value={form.full_name} onChange={(e) => updateField("full_name", e.target.value)} required minLength={2}/></Field>
                    <Field label="Số điện thoại"><input className="ta-input" value={form.phone} onChange={(e) => updateField("phone", e.target.value)} placeholder="0901 234 567"/></Field>
                    <Field label="Ngày sinh"><input className="ta-input" type="date" value={form.date_of_birth} onChange={(e) => updateField("date_of_birth", e.target.value)}/></Field>
                    <Field label="Giới tính"><select className="ta-input" value={form.gender} onChange={(e) => updateField("gender", e.target.value)}><option value="male">Nam</option><option value="female">Nữ</option><option value="non_binary">Khác</option><option value="prefer_not_to_say">Không muốn tiết lộ</option></select></Field>
                    <Field label="Địa chỉ" wide><input className="ta-input" value={form.address} onChange={(e) => updateField("address", e.target.value)} placeholder="Số nhà, đường, phường/xã"/></Field>
                    <Field label="Thành phố"><input className="ta-input" value={form.city} onChange={(e) => updateField("city", e.target.value)}/></Field>
                    <Field label="Quốc gia"><input className="ta-input" value={form.country} onChange={(e) => updateField("country", e.target.value)}/></Field>
                    <Field label="Giới thiệu" wide><textarea className="ta-input" rows={4} value={form.bio} onChange={(e) => updateField("bio", e.target.value)} placeholder="Một vài thông tin về bạn..."/></Field>
                    <Field label="Người liên hệ khẩn cấp"><input className="ta-input" value={form.emergency_contact_name} onChange={(e) => updateField("emergency_contact_name", e.target.value)}/></Field>
                    <Field label="SĐT liên hệ khẩn cấp"><input className="ta-input" value={form.emergency_contact_phone} onChange={(e) => updateField("emergency_contact_phone", e.target.value)}/></Field>
                  </div>

                  <h2 style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 16, fontWeight: 800, margin: "25px 0 15px" }}><Heart size={18} color="#EC4899"/> Sở thích & cách làm việc</h2>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 13 }}>
                    <Field label="Sở thích" wide><input className="ta-input" value={form.hobbies} onChange={(e) => updateField("hobbies", e.target.value)} placeholder="Đọc sách, chạy bộ, du lịch..."/><small style={{ color: "var(--text-light)" }}>Phân cách bằng dấu phẩy.</small></Field>
                    <Field label="Ngôn ngữ ưu tiên"><select className="ta-input" value={form.preferred_language} onChange={(e) => updateField("preferred_language", e.target.value)}><option value="vi">Tiếng Việt</option><option value="ja">Tiếng Nhật</option><option value="en">English</option></select></Field>
                    <Field label="Múi giờ"><input className="ta-input" value={form.timezone} onChange={(e) => updateField("timezone", e.target.value)}/></Field>
                    <Field label="Phong cách làm việc"><input className="ta-input" value={form.work_style} onChange={(e) => updateField("work_style", e.target.value)} placeholder="Tập trung sâu, linh hoạt..."/></Field>
                    <Field label="Giao diện"><select className="ta-input" value={form.theme} onChange={(e) => updateField("theme", e.target.value as ProfileForm["theme"])}><option value="system">Theo hệ thống</option><option value="light">Sáng</option><option value="dark">Tối</option></select></Field>
                  </div>
                  <button className="ta-btn ta-btn-primary" disabled={saving} style={{ marginTop: 22 }}><Save size={15}/> {saving ? "Đang lưu..." : "Lưu thay đổi"}</button>
                </form>

                <aside style={{ display: "grid", gap: 14 }}>
                  <section className="ta-card" style={{ padding: 19 }}>
                    <h3 style={{ display: "flex", alignItems: "center", gap: 7, fontWeight: 800 }}><Wallet size={17} color="#10B981"/> Thông tin nhân sự</h3>
                    <InfoRow label="Chức danh" value={profile.employment.job_title || "Chưa thiết lập"}/>
                    <InfoRow label="Mã nhân viên" value={profile.employment.employee_code || "Chưa thiết lập"}/>
                    <InfoRow label="Ngày vào làm" value={profile.employment.hire_date ? new Date(profile.employment.hire_date).toLocaleDateString("vi-VN") : "Chưa thiết lập"}/>
                    <InfoRow label="Lương tháng" value={salary == null ? "Chưa thiết lập" : new Intl.NumberFormat("vi-VN", { style: "currency", currency: profile.employment.salary_currency || "VND" }).format(salary)}/>
                    <p style={{ fontSize: 11, color: "#94A3B8", marginTop: 13, lineHeight: 1.45 }}>Thông tin lương, chức danh và phép do Owner/Admin/HR quản lý.</p>
                  </section>
                  <section className="ta-card" style={{ padding: 19 }}>
                    <h3 style={{ display: "flex", alignItems: "center", gap: 7, fontWeight: 800 }}><MapPin size={17} color="#F59E0B"/> Ngày nghỉ phép</h3>
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 8, marginTop: 14 }}>
                      <Metric label="Tổng" value={leave?.total_days ?? 0}/><Metric label="Đã dùng" value={leave?.used_days ?? 0}/><Metric label="Còn lại" value={leave?.remaining_days ?? 0}/>
                    </div>
                  </section>
                </aside>
              </div>
            </>
          )}
        </div>
      </main>
      {cropFile && (
        <AvatarCropper
          file={cropFile}
          busy={uploading}
          onCancel={() => setCropFile(null)}
          onApply={uploadAvatar}
        />
      )}
    </div>
  );
}

function Field({ label, wide = false, children }: { label: string; wide?: boolean; children: React.ReactNode }) {
  return <label style={{ display: "grid", gap: 5, gridColumn: wide ? "1 / -1" : undefined, fontSize: 12, fontWeight: 650, color: "var(--text-dark)" }}>{label}{children}</label>;
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return <div style={{ display: "flex", justifyContent: "space-between", gap: 12, padding: "11px 0", borderBottom: "1px solid #F1F5F9", fontSize: 12 }}><span style={{ color: "#64748B" }}>{label}</span><strong style={{ textAlign: "right" }}>{value}</strong></div>;
}

function Metric({ label, value }: { label: string; value: number }) {
  return <div style={{ borderRadius: 10, background: "#F8FAFC", padding: "11px 6px", textAlign: "center" }}><strong style={{ display: "block", fontSize: 18, color: "#1E293B" }}>{value}</strong><span style={{ fontSize: 10, color: "#64748B" }}>{label}</span></div>;
}
