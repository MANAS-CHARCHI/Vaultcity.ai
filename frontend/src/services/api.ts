/**
 * Vault City API Service & Strict Security Token Interceptor
 * 
 * Token Security Architecture (Zero-Token in Browser Storage):
 * - Access Token: 5-minute expiration, delivered strictly via HttpOnly cookie (cookie_access_token).
 * - Refresh Token: 7-day expiration, delivered strictly via HttpOnly cookie (cookie_refresh_token).
 * - In-Browser Storage: Absolutely NO tokens are stored in localStorage or sessionStorage (protects against XSS).
 * - All network calls include credentials: 'include' so cookies are managed and sent automatically by the browser.
 * - If 401 Unauthorized occurs, silently refreshes via POST /api/v1/auth/refresh (cookie-driven).
 * - If refresh fails, triggers forced logout immediately.
 */
import {
  ActivityItem,
  AuthResponseData,
  AuthTokens,
  DocumentFile,
  SecurityAlert,
  UserProfile,
  UserSession,
} from '../types';

const API_BASE = '/api/v1';

export const USER_KEY = 'vault_user';
export const ACCESS_TOKEN_KEY = 'vault_access_token';
export const REFRESH_TOKEN_KEY = 'vault_refresh_token';

type LogoutListener = (reason: string) => void;
const logoutListeners: Set<LogoutListener> = new Set();

/** Register a listener callback invoked whenever a forced logout occurs */
export function onForcedLogout(listener: LogoutListener): () => void {
  logoutListeners.add(listener);
  return () => logoutListeners.delete(listener);
}

/**
 * Scrub any legacy token keys from localStorage immediately to eliminate XSS exposure.
 */
function purgeLegacyTokens(): void {
  try {
    localStorage.removeItem(ACCESS_TOKEN_KEY);
    localStorage.removeItem(REFRESH_TOKEN_KEY);
  } catch (e) {
    // Ignore storage errors in restricted contexts
  }
}

// Purge any leftover tokens on application script load
purgeLegacyTokens();

/** Check if active user profile exists in client cache */
export function hasActiveSession(): boolean {
  return Boolean(getStoredUser());
}

/**
 * Compatibility alias: tokens are now strictly HttpOnly cookies and never stored in browser storage.
 */
export const hasValidTokens = hasActiveSession;

/** @deprecated Tokens are strictly HttpOnly cookies and inaccessible to JavaScript */
export function getAccessToken(): string | null {
  return null;
}

/** @deprecated Tokens are strictly HttpOnly cookies and inaccessible to JavaScript */
export function getRefreshToken(): string | null {
  return null;
}

/** Retrieve cached user profile (non-sensitive display metadata) */
export function getStoredUser(): UserProfile | null {
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

/**
 * Store user profile in localStorage for fast UI rendering.
 * NEVER stores tokens — tokens are strictly HttpOnly cookies managed by the browser engine.
 */
export function setAuthSession(_tokens?: AuthTokens, user?: UserProfile): void {
  purgeLegacyTokens();
  if (user) {
    localStorage.setItem(USER_KEY, JSON.stringify(user));
  }
}

/** Immediately purge all cached user state and any legacy tokens */
export function clearAuthSession(): void {
  purgeLegacyTokens();
  localStorage.removeItem(USER_KEY);
  try {
    for (let i = localStorage.length - 1; i >= 0; i--) {
      const key = localStorage.key(i);
      if (
        key &&
        (key.startsWith("vault_activities_cache_") ||
          key.toLowerCase().includes("infected") ||
          key.toLowerCase().includes("virus") ||
          key.toLowerCase().includes("malware") ||
          key.toLowerCase().includes("eicar"))
      ) {
        localStorage.removeItem(key);
      }
    }
  } catch (_) {}
}

/**
 * Trigger an immediate, uncompromising forced logout across the entire application.
 * Clears local state and notifies all listeners and other browser tabs.
 */
export function triggerForcedLogout(reason = 'Authentication required'): void {
  clearAuthSession();

  // Notify registered callbacks
  logoutListeners.forEach((listener) => {
    try {
      listener(reason);
    } catch (e) {
      console.error('Logout listener error:', e);
    }
  });

  // Dispatch custom window event
  window.dispatchEvent(
    new CustomEvent('vault:unauthorized', { detail: { reason } })
  );
}

// Track in-flight refresh promise to prevent duplicate concurrent refresh requests
let refreshPromise: Promise<boolean> | null = null;

/**
 * Execute silent refresh against backend using the HttpOnly refresh token cookie.
 * Rotates both access_token and refresh_token directly in HttpOnly cookies.
 * Does NOT receive or persist any token in JavaScript storage.
 * If refresh token is present and valid, user is NEVER logged out.
 */
export async function apiRefreshToken(): Promise<boolean> {
  if (refreshPromise) {
    return refreshPromise;
  }

  refreshPromise = (async () => {
    try {
      const res = await fetch(`${API_BASE}/auth/refresh`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        credentials: 'include',
      });

      if (!res.ok) {
        // ONLY trigger forced logout if backend explicitly confirms refresh token is revoked/expired (401 or 403)
        if (res.status === 401 || res.status === 403) {
          triggerForcedLogout('Refresh token expired or revoked');
        }
        return false;
      }

      const data: AuthResponseData = await res.json();
      if (data.user) {
        const user: UserProfile = {
          id: data.user.id,
          email: data.user.email,
          name: data.user.username || data.user.email.split('@')[0],
          fullName: data.user.username || data.user.email.split('@')[0],
          username: data.user.username,
          role: (data.user.role as 'user' | 'admin') || 'user',
          hasGoogle: data.user.has_google,
          hasPassword: data.user.has_password,
        };
        setAuthSession(undefined, user);
      }

      return true;
    } catch (err) {
      console.warn('Silent refresh network glitch (will retry):', err);
      // Do NOT log out on network hiccup — preserve user session and cookies
      return false;
    } finally {
      refreshPromise = null;
    }
  })();

  return refreshPromise;
}

// Internal compatibility alias
const silentRefreshToken = apiRefreshToken;

/**
 * Centralized fetch interceptor:
 * 1. Transmits and receives HttpOnly cookies with credentials: 'include'
 * 2. Catches 401 Unauthorized responses
 * 3. Attempts silent refresh via HttpOnly refresh cookie
 * 4. Retries original request upon successful cookie rotation
 * 5. Forces logout immediately if refresh fails
 */
export async function apiFetch(
  endpoint: string,
  options: RequestInit = {}
): Promise<Response> {
  const url = endpoint.startsWith('http') ? endpoint : `${API_BASE}${endpoint}`;
  const headers = new Headers(options.headers || {});

  let response: Response;
  try {
    response = await fetch(url, { ...options, headers, credentials: 'include' });
  } catch (networkError) {
    throw networkError;
  }

  // Handle 401 Unauthorized (Expired / Revoked / Invalid Token)
  if (
    response.status === 401 &&
    !endpoint.includes('/auth/login') &&
    !endpoint.includes('/auth/register')
  ) {
    // If we're already calling refresh and it failed, do not loop
    if (endpoint.includes('/auth/refresh')) {
      triggerForcedLogout('Refresh token expired or revoked');
      return response;
    }

    // Initiate or share active token refresh
    if (!refreshPromise) {
      refreshPromise = silentRefreshToken().finally(() => {
        refreshPromise = null;
      });
    }

    const refreshSuccess = await refreshPromise;
    if (!refreshSuccess) {
      // Refresh failed, user is logged out
      return response;
    }

    // Retry original request (browser automatically transmits the newly set HttpOnly cookie)
    return fetch(url, { ...options, headers, credentials: 'include' });
  }

  return response;
}

/**
 * Verify if current user is authorized with backend (GET /api/v1/auth/me).
 * Strict verification: if credentials invalid or 401, forces immediate logout.
 */
export async function verifyAuthSession(): Promise<UserProfile | null> {
  try {
    let res = await apiFetch('/auth/me');

    // If 401 occurred and retry didn't resolve, explicitly try refreshing token
    if (res.status === 401) {
      const refreshed = await apiRefreshToken();
      if (refreshed) {
        res = await fetch(`${API_BASE}/auth/me`, { credentials: 'include' });
      }
    }

    if (!res.ok) {
      // Only logout if explicitly 401/403 (unauthenticated)
      if (res.status === 401 || res.status === 403) {
        triggerForcedLogout('Session verification failed');
      }
      return null;
    }

    const userData = await res.json();
    const user: UserProfile = {
      id: userData.id,
      email: userData.email,
      name: userData.username || userData.email.split('@')[0],
      fullName: userData.username || userData.email.split('@')[0],
      username: userData.username,
      role: userData.role || 'user',
      hasGoogle: userData.has_google,
      hasPassword: userData.has_password,
    };

    setAuthSession(undefined, user);
    return user;
  } catch (e) {
    console.warn('verifyAuthSession network error:', e);
    // If completely offline or network glitch, fall back to cached user profile
    return getStoredUser();
  }
}

/** User Login with Email or Username & Password */
export async function apiLogin(payload: {
  email: string;
  password: string;
  turnstile_token?: string;
}): Promise<{ user: UserProfile; tokens: AuthTokens }> {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify(payload),
  });

  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail || 'Authentication failed. Please verify credentials.');
  }

  const user: UserProfile = {
    id: data.user.id,
    email: data.user.email,
    name: data.user.username || data.user.email.split('@')[0],
    fullName: data.user.username || data.user.email.split('@')[0],
    username: data.user.username,
    role: data.user.role || 'user',
    hasGoogle: data.user.has_google,
    hasPassword: data.user.has_password,
  };

  setAuthSession(undefined, user);
  return { user, tokens: { accessToken: '', refreshToken: '', tokenType: 'cookie' } };
}

/** User Registration (triggers 6-digit email OTP verification or links Google account) */
export async function apiRegister(payload: {
  email: string;
  password: string;
  username?: string;
  turnstile_token?: string;
}): Promise<{
  verificationRequired: boolean;
  email: string;
  message: string;
  expiresInSeconds?: number;
  cooldownSeconds?: number;
  maxResends?: number;
  remainingResends?: number;
  devOtp?: string;
  user?: UserProfile;
  tokens?: AuthTokens;
}> {
  const res = await fetch(`${API_BASE}/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify(payload),
  });

  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail || 'Registration failed.');
  }

  if (data.verification_required) {
    return {
      verificationRequired: true,
      email: data.email || payload.email,
      message: data.message || 'Verification code sent to your email.',
      expiresInSeconds: data.expires_in_seconds,
      cooldownSeconds: data.cooldown_seconds ?? 60,
      maxResends: data.max_resends ?? 5,
      remainingResends: data.remaining_resends ?? 5,
      devOtp: data.dev_otp || undefined,
    };
  }

  const user: UserProfile = {
    id: data.user.id,
    email: data.user.email,
    name: data.user.username || data.user.email.split('@')[0],
    fullName: data.user.username || data.user.email.split('@')[0],
    username: data.user.username,
    role: data.user.role || 'user',
    isEmailVerified: data.user.is_email_verified ?? true,
    hasGoogle: data.user.has_google,
    hasPassword: data.user.has_password,
  };

  setAuthSession(undefined, user);
  return {
    verificationRequired: false,
    email: user.email,
    message: data.message || 'Account ready.',
    user,
    tokens: { accessToken: '', refreshToken: '', tokenType: 'cookie' },
  };
}

/** Verify 6-digit email OTP to activate account and establish session */
export async function apiVerifyEmailOTP(payload: {
  email: string;
  otp: string;
}): Promise<{ user: UserProfile; tokens: AuthTokens }> {
  const res = await fetch(`${API_BASE}/auth/verify-email`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify(payload),
  });

  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail || 'Verification failed. Please check your 6-digit code.');
  }

  const user: UserProfile = {
    id: data.user.id,
    email: data.user.email,
    name: data.user.username || data.user.email.split('@')[0],
    fullName: data.user.username || data.user.email.split('@')[0],
    username: data.user.username,
    role: data.user.role || 'user',
    isEmailVerified: data.user.is_email_verified ?? true,
    hasGoogle: data.user.has_google,
    hasPassword: data.user.has_password,
  };

  setAuthSession(undefined, user);
  return { user, tokens: { accessToken: '', refreshToken: '', tokenType: 'cookie' } };
}

/** Resend 6-digit onboarding OTP */
export async function apiResendOTP(payload: {
  email: string;
  turnstile_token?: string;
}): Promise<{
  status: string;
  message: string;
  expires_in_seconds: number;
  cooldown_seconds: number;
  resends_used?: number;
  max_resends?: number;
  remaining_resends?: number;
  dev_otp?: string;
}> {
  const res = await fetch(`${API_BASE}/auth/resend-otp`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify(payload),
  });

  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail || 'Unable to resend verification code.');
  }
  return data;
}

/** Google Social Authentication with Google Identity Services ID Token */
export async function apiGoogleAuth(payload: {
  id_token: string;
  turnstile_token?: string;
}): Promise<{ user: UserProfile; tokens: AuthTokens }> {
  const res = await fetch(`${API_BASE}/auth/google`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify(payload),
  });

  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail || 'Google authentication failed.');
  }

  const user: UserProfile = {
    id: data.user.id,
    email: data.user.email,
    name: data.user.username || data.user.email.split('@')[0],
    fullName: data.user.username || data.user.email.split('@')[0],
    username: data.user.username,
    role: data.user.role || 'user',
    hasGoogle: data.user.has_google,
    hasPassword: data.user.has_password,
  };

  setAuthSession(undefined, user);
  return { user, tokens: { accessToken: '', refreshToken: '', tokenType: 'cookie' } };
}

/** Request password reset instructions / token via email or username */
export async function apiForgotPassword(payload: {
  email: string;
  turnstile_token?: string;
}): Promise<{ status: string; message: string; reset_token?: string; username?: string }> {
  const res = await fetch(`${API_BASE}/auth/forgot-password`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify(payload),
  });

  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail || 'Failed to dispatch password recovery instructions.');
  }
  return data;
}

/** Complete password reset using valid cryptographic token */
export async function apiResetPassword(payload: {
  token: string;
  new_password: string;
  turnstile_token?: string;
}): Promise<{ user?: UserProfile; message: string; access_token?: string }> {
  const res = await fetch(`${API_BASE}/auth/reset-password`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify(payload),
  });

  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail || 'Failed to reset password. Token may have expired or is invalid.');
  }

  let user: UserProfile | undefined = undefined;
  if (data.user) {
    user = {
      id: data.user.id,
      email: data.user.email,
      name: data.user.username || data.user.email.split('@')[0],
      fullName: data.user.username || data.user.email.split('@')[0],
      username: data.user.username,
      role: (data.user.role as 'user' | 'admin') || 'user',
      hasGoogle: data.user.has_google,
      hasPassword: data.user.has_password,
    };
    setAuthSession(undefined, user);
  }

  return {
    user,
    message: data.message || 'Password successfully updated.',
    access_token: data.access_token,
  };
}

/** Change or set master password for authenticated user (verified via HttpOnly access token) */
export async function apiChangePassword(payload: {
  current_password?: string;
  new_password: string;
}): Promise<UserProfile> {
  const res = await apiFetch('/auth/change-password', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });

  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail || 'Failed to change password.');
  }

  const user: UserProfile = {
    id: data.id,
    email: data.email,
    name: data.username || data.email.split('@')[0],
    fullName: data.username || data.email.split('@')[0],
    username: data.username,
    role: data.role || 'user',
    hasGoogle: data.has_google,
    hasPassword: data.has_password,
  };

  setAuthSession(undefined, user);
  return user;
}

/** Update user display username in PostgreSQL (verified via HttpOnly access token) */
export async function apiUpdateProfile(username: string): Promise<UserProfile> {
  const res = await apiFetch('/auth/profile', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username }),
  });

  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail || 'Failed to update username.');
  }

  const user: UserProfile = {
    id: data.id,
    email: data.email,
    name: data.username || data.email.split('@')[0],
    fullName: data.username || data.email.split('@')[0],
    username: data.username,
    role: data.role || 'user',
    hasGoogle: data.has_google,
    hasPassword: data.has_password,
  };

  setAuthSession(undefined, user);
  return user;
}

/** User Logout (Revokes tokens on server & clears local state) */
export async function apiLogout(): Promise<void> {
  try {
    await apiFetch('/auth/logout', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
  } catch (err) {
    console.warn('Server logout error (clearing local session anyway):', err);
  } finally {
    triggerForcedLogout('User logged out');
  }
}

/** Fetch all active device sessions for the authenticated user */
export async function apiGetSessions(): Promise<UserSession[]> {
  const res = await apiFetch('/auth/sessions');
  if (!res.ok) {
    throw new Error('Failed to load active sessions.');
  }
  return await res.json();
}

/** Log out a single specific device session */
export async function apiRevokeSession(sessionId: string): Promise<void> {
  const res = await apiFetch(`/auth/sessions/${sessionId}`, {
    method: 'DELETE',
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || 'Failed to revoke session.');
  }
}

/** Log out all devices (optionally keep current session active) */
export async function apiRevokeAllSessions(keepCurrent = false): Promise<void> {
  const res = await apiFetch('/auth/sessions/revoke-all', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ keep_current: keepCurrent }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || 'Failed to revoke all sessions.');
  }
}

/** Check system health ready status */
export async function checkHealth() {
  try {
    const res = await fetch(`${API_BASE}/health/ready`, { credentials: 'include' });
    return await res.json();
  } catch (error) {
    return { status: 'error', checks: { error: String(error) } };
  }
}

// --- Real Vault Files & Activity API ---

function mapApiFileToDocument(raw: any): DocumentFile {
  const validCategories = ['Identity', 'Financial', 'Medical', 'Personal', 'Other'] as const;
  const cat = validCategories.includes(raw.category) ? raw.category : 'Other';
  const statusVal =
    raw.status === 'scanning' || raw.status === 'classifying' || raw.status === 'indexed' || raw.status === 'failed'
      ? raw.status
      : 'indexed';

  return {
    id: raw.id,
    name: raw.filename,
    originalName: raw.original_filename,
    size: Number(raw.file_size || 0),
    mimeType: raw.mime_type || 'application/octet-stream',
    uploadedAt: raw.created_at || new Date().toISOString(),
    status: statusVal,
    scanStatus: raw.scan_status,
    documentType: raw.document_type || undefined,
    category: cat,
    description: raw.description || undefined,
    scanReport: raw.scan_report || undefined,
    isPii: Boolean(raw.is_pii),
    s3Key: raw.s3_key || undefined,
    sha256: raw.sha256_hash || undefined,
    previewUrl: raw.preview_url || undefined,
    thumbnailUrl: raw.preview_url || undefined,
    downloadUrl: raw.download_url || undefined,
    pageCount: Number(raw.page_count || 1),
  };
}

/** Upload a single file to Quarantine S3 -> Scan -> Promote to Secured S3 & pgvector */
export async function apiUploadFile(params: {
  file: File;
  customName?: string;
  category?: string;
}): Promise<DocumentFile> {
  const formData = new FormData();
  formData.append('file', params.file);
  if (params.customName) {
    formData.append('custom_name', params.customName);
  }
  if (params.category) {
    formData.append('category', params.category);
  }

  const res = await apiFetch('/files/upload', {
    method: 'POST',
    body: formData,
  });

  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || `Failed to upload ${params.file.name}.`);
  }

  return mapApiFileToDocument(data);
}

/** Fetch all vault files and storage quota stats for the authenticated user */
export async function apiListFiles(): Promise<{
  files: DocumentFile[];
  totalCount: number;
  usedBytes: number;
  quotaBytes: number;
  securityAlerts: SecurityAlert[];
}> {
  const res = await apiFetch('/files');
  if (!res.ok) {
    throw new Error('Failed to load vault files.');
  }
  const data = await res.json();
  return {
    files: Array.isArray(data.files) ? data.files.map(mapApiFileToDocument) : [],
    totalCount: Number(data.total_count || 0),
    usedBytes: Number(data.used_bytes || 0),
    quotaBytes: Number(data.quota_bytes || 100 * 1024 * 1024),
    securityAlerts: Array.isArray(data.security_alerts)
      ? data.security_alerts.map((a: any) => ({
          id: String(a.id),
          filename: String(a.filename || 'File'),
          threatName: String(a.threat_name || 'Malware detected'),
          createdAt: String(a.created_at || ''),
          message: String(
            a.message ||
              `Security Alert: '${a.filename || 'File'}' was infected and has been automatically removed from your vault.`
          ),
        }))
      : [],
  };
}

/** Dismiss an infected file security alert notification */
export async function apiDismissSecurityAlert(alertId: string): Promise<void> {
  await apiFetch(`/files/alerts/${alertId}/dismiss`, {
    method: 'POST',
  }).catch(() => {});
}

/** Rename or update metadata of a stored vault file */
export async function apiRenameFile(fileId: string, filename: string): Promise<DocumentFile> {
  const res = await apiFetch(`/files/${fileId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filename }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || 'Failed to rename file.');
  }
  return mapApiFileToDocument(data);
}

/** Permanently delete a file from S3, pgvector, and PostgreSQL */
export async function apiDeleteFile(fileId: string): Promise<void> {
  const res = await apiFetch(`/files/${fileId}`, {
    method: 'DELETE',
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || 'Failed to delete file.');
  }
}

/** Download a verified file from Secured S3 through authenticated session */
export async function apiDownloadFile(fileId: string, filename: string): Promise<void> {
  const res = await apiFetch(`/files/${fileId}/content?download=true`);
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || 'Unable to download file.');
  }
  const blob = await res.blob();
  const blobUrl = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = blobUrl;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(blobUrl), 5000);
}

/** Fetch user's real security & file audit logs with pagination from database */
export async function apiGetActivityLogs(
  page = 1,
  limit = 10
): Promise<{ activities: ActivityItem[]; totalCount: number; hasMore: boolean; page: number }> {
  const res = await apiFetch(`/files/activity/logs?page=${page}&limit=${limit}`);
  if (!res.ok) {
    return { activities: [], totalCount: 0, hasMore: false, page };
  }
  const data = await res.json();
  return {
    activities: Array.isArray(data.activities) ? data.activities : [],
    totalCount: Number(data.total_count || 0),
    hasMore: Boolean(data.has_more),
    page: Number(data.page || page),
  };
}

/** Send a chat query to Vault Assistant powered by Smart Agentic RAG */
export async function apiSendChatMessage(content: string, conversationId?: string): Promise<{
  id: string;
  sender: 'assistant';
  content: string;
  sources: string[];
  interactive_options?: import('../types').InteractiveOption[];
  strategy_used?: string;
  tokens_used: number;
  created_at: string;
}> {
  const res = await apiFetch('/chat/message', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content, conversation_id: conversationId }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || 'Assistant query failed.');
  }
  return res.json();
}

/** Fetch user's Claude token usage breakdown (input vs output tokens per model) */
export async function apiGetTokenUsage(): Promise<import('../types').UserTokenSummary | null> {
  const res = await apiFetch('/tokens/my-usage');
  if (!res.ok) return null;
  return res.json();
}
