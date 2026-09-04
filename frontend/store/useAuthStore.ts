/**
 * Zustand store for global authentication state with Access Token (LocalStorage/RAM)
 * and Refresh Token (HttpOnly Cookie), featuring bulletproof F5 hydration protection.
 */

import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import axios from 'axios';
import api from '@/lib/api';

interface UserInfo {
  id: string;
  email: string;
  full_name: string;
  /** Nhãn nội bộ. Để hiển thị cho người dùng, ưu tiên `position_name`. */
  role: string;
  /** Tên chức vụ do công ty tự đặt; null nếu tài khoản chưa được gán chức vụ. */
  position_name?: string | null;
  department: string;
  tenant_id: string;
  avatar_url?: string | null;
}

interface AuthState {
  user: UserInfo | null;
  accessToken: string | null;
  isAuthenticated: boolean;
  hasHydrated: boolean;

  setHasHydrated: (status: boolean) => void;
  login: (email: string, password: string) => Promise<void>;
  register: (
    email: string,
    fullName: string,
    password: string,
    tenantName: string
  ) => Promise<void>;
  logout: () => Promise<void>;
  fetchMe: () => Promise<void>;
  setTokens: (access: string, user: UserInfo, refresh?: string) => void;
}

const getInitialToken = (): string | null => {
  if (typeof window !== 'undefined') {
    return localStorage.getItem('access_token');
  }
  return null;
};

const initialToken = getInitialToken();

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      user: null,
      accessToken: initialToken,
      isAuthenticated: Boolean(initialToken),
      hasHydrated: false,

      setHasHydrated: (status) => set({ hasHydrated: status }),

      setTokens: (access, user, refresh?: string) => {
        if (typeof window !== 'undefined') {
          localStorage.setItem('access_token', access);
          if (refresh) localStorage.setItem('refresh_token', refresh);
        }
        set({ accessToken: access, user, isAuthenticated: true });
      },

      login: async (email, password) => {
        const { data } = await api.post('/api/v1/auth/login', { email, password });
        if (typeof window !== 'undefined') {
          if (data.access_token) localStorage.setItem('access_token', data.access_token);
          if (data.refresh_token) localStorage.setItem('refresh_token', data.refresh_token);
        }
        set({
          accessToken: data.access_token,
          user: data.user,
          isAuthenticated: true,
        });
      },

      register: async (email, fullName, password, tenantName) => {
        const { data } = await api.post('/api/v1/auth/register', {
          email,
          full_name: fullName,
          password,
          tenant_name: tenantName,
        });
        if (typeof window !== 'undefined') {
          if (data.access_token) localStorage.setItem('access_token', data.access_token);
          if (data.refresh_token) localStorage.setItem('refresh_token', data.refresh_token);
        }
        set({
          accessToken: data.access_token,
          user: data.user,
          isAuthenticated: true,
        });
      },

      fetchMe: async () => {
        const token = get().accessToken || (typeof window !== 'undefined' ? localStorage.getItem('access_token') : null);
        if (!token) {
          set({ user: null, isAuthenticated: false });
          return;
        }
        try {
          const { data } = await api.get('/api/v1/users/me');
          set({ user: data, isAuthenticated: true });
        } catch (err: unknown) {
          console.error("fetchMe failed:", err);
          // Only clear if server explicitly returned 401 Unauthorized
          if (axios.isAxiosError(err) && err.response?.status === 401) {
            if (typeof window !== 'undefined') {
              localStorage.removeItem('access_token');
              localStorage.removeItem('refresh_token');
            }
            set({ user: null, accessToken: null, isAuthenticated: false });
          }
        }
      },

      logout: async () => {
        try {
          await api.post('/api/v1/auth/logout');
        } catch (err) {
          console.error('Logout failed on backend:', err);
        } finally {
          if (typeof window !== 'undefined') {
            localStorage.removeItem('access_token');
            localStorage.removeItem('refresh_token');
          }
          set({ user: null, accessToken: null, isAuthenticated: false });
        }
      },
    }),
    {
      name: 'ai-workforce-auth',
      onRehydrateStorage: () => (state) => {
        if (state) {
          state.setHasHydrated(true);
          const savedToken = typeof window !== 'undefined' ? localStorage.getItem('access_token') : null;
          if (savedToken) {
            state.fetchMe();
          }
        }
      },
      partialize: (state) => ({
        user: state.user,
        accessToken: state.accessToken,
        isAuthenticated: state.isAuthenticated,
      }),
    }
  )
);
