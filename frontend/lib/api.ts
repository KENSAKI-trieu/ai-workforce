/**
 * Axios API client.
 *
 * The access token lives in localStorage and is sent as a Bearer header. The refresh
 * token is never visible here: the backend returns it only as an HttpOnly cookie scoped
 * to /api/v1/auth, so `withCredentials` is what makes a refresh work, and nothing on
 * this page can read or forward the credential.
 */

import axios, { AxiosError, InternalAxiosRequestConfig } from 'axios';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export const api = axios.create({
  baseURL: API_BASE,
  withCredentials: true, // Sends the HttpOnly refresh_token cookie on /api/v1/auth calls
  // Document embedding and other AI pipelines can legitimately take several
  // minutes. Axios uses 0 to disable the client-side response timeout.
  timeout: 0,
});

export const getAccessToken = (): string | null =>
  typeof window === 'undefined' ? null : localStorage.getItem('access_token');

const clearStoredSession = () => {
  if (typeof window === 'undefined') return;
  localStorage.removeItem('access_token');
};

const redirectToLogin = () => {
  if (typeof window === 'undefined') return;
  if (window.location.pathname !== '/login' && window.location.pathname !== '/register') {
    window.location.href = '/login';
  }
};

// ── Request interceptor: Inject Access Token from localStorage ──
api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = getAccessToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

/**
 * Exchange the refresh cookie for a new access token.
 *
 * Concurrent callers share one in-flight request: several requests failing at once must
 * not each rotate the token, because rotation invalidates the previous refresh token and
 * a second rotation with a stale one now reads as replay and revokes the whole session.
 *
 * Exported because the SSE helpers use bare `fetch` and never touch the interceptor
 * below; without this they would simply fail on an expired token.
 */
let refreshInFlight: Promise<string> | null = null;

export function refreshAccessToken(): Promise<string> {
  if (!refreshInFlight) {
    refreshInFlight = axios
      // No body: the server reads the cookie and rejects anything else.
      .post(`${API_BASE}/api/v1/auth/refresh`, null, { withCredentials: true })
      .then(async ({ data }) => {
        const newAccessToken: string | undefined = data?.access_token;
        if (!newAccessToken) {
          throw new Error('Refresh response carried no access token');
        }
        if (typeof window !== 'undefined') {
          localStorage.setItem('access_token', newAccessToken);
        }
        api.defaults.headers.common.Authorization = `Bearer ${newAccessToken}`;

        try {
          const { useAuthStore } = await import('@/store/useAuthStore');
          if (data.user) {
            useAuthStore.getState().setTokens(newAccessToken, data.user);
          } else {
            useAuthStore.setState({ accessToken: newAccessToken, isAuthenticated: true });
          }
        } catch (e) {
          console.warn('Could not sync useAuthStore during refresh:', e);
        }
        return newAccessToken;
      })
      .finally(() => {
        refreshInFlight = null;
      });
  }
  return refreshInFlight;
}

// ── Response interceptor: Silent Token Refresh on 401 ──
api.interceptors.response.use(
  (res) => res,
  async (error: AxiosError) => {
    const originalRequest = error.config as InternalAxiosRequestConfig & { _retry?: boolean };

    if (
      error.response?.status === 401 &&
      originalRequest &&
      !originalRequest._retry &&
      !originalRequest.url?.includes('/auth/login') &&
      !originalRequest.url?.includes('/auth/register')
    ) {
      if (originalRequest.url?.includes('/auth/refresh')) {
        clearStoredSession();
        redirectToLogin();
        return Promise.reject(error);
      }

      originalRequest._retry = true;

      try {
        const newAccessToken = await refreshAccessToken();
        originalRequest.headers.Authorization = `Bearer ${newAccessToken}`;
        return api(originalRequest);
      } catch (refreshErr) {
        clearStoredSession();
        redirectToLogin();
        return Promise.reject(refreshErr);
      }
    }

    return Promise.reject(error);
  }
);

export default api;
