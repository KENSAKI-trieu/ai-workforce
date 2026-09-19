/**
 * `fetch` with the same silent-refresh behaviour as the axios client.
 *
 * The SSE helpers cannot go through axios -- they need the streaming body -- so they
 * called `fetch` directly with whatever access token happened to be in localStorage.
 * That was survivable while access tokens lasted a week; now that they last 30 minutes,
 * a stream started on a stale token would just fail with 401 and no recovery, because
 * bare `fetch` never touches the axios response interceptor.
 */

import { getAccessToken, refreshAccessToken } from '@/lib/api';

/**
 * Send a request, and on 401 refresh the session once and send it again.
 *
 * `init.body` must be replayable (a string or a plain object, not a one-shot stream),
 * since the retry re-sends it.
 */
export async function authorizedFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> {
  const send = (token: string | null) =>
    fetch(input, {
      ...init,
      // The refresh cookie is HttpOnly; the browser attaches it only if we ask.
      credentials: 'include',
      headers: {
        ...(init.headers as Record<string, string> | undefined),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    });

  const response = await send(getAccessToken());
  if (response.status !== 401) {
    return response;
  }

  let refreshed: string;
  try {
    refreshed = await refreshAccessToken();
  } catch {
    // Refresh failed: hand back the original 401 so the caller reports the real problem
    // rather than a confusing error about the refresh.
    return response;
  }
  return send(refreshed);
}
