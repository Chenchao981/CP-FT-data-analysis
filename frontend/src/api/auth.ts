export type PermissionCode =
  | "TASK_CREATE"
  | "TASK_RETRY"
  | "DATASET_READ"
  | "DATASET_PUBLISH"
  | "ANALYSIS_RUN"
  | "EXPORT_DATA"
  | "FORMAT_GOVERN"
  | "RULE_GOVERN"
  | "DQ_WAIVE_ERROR"
  | "AUDIT_READ"
  | "MANAGEMENT_READ"
  | "USER_ADMIN";
export type CurrentUser = { user_id: number; login_name: string; display_name: string; department_code: string | null; roles: string[]; permissions: string[] };
export type UserRecord = CurrentUser & { email: string | null; status: "PENDING" | "ACTIVE" | "LOCKED" | "DISABLED"; created_at_utc: string; last_login_at_utc: string | null };
export type LoginResult = { access_token: string; token_type: "bearer"; expires_at_utc: string; user: CurrentUser };
const TOKEN_KEY = "tms_access_token";
export const storedToken = () => typeof localStorage === "undefined" ? null : localStorage.getItem(TOKEN_KEY);
export const saveToken = (token: string) => { if (typeof localStorage !== "undefined") localStorage.setItem(TOKEN_KEY, token); };
export const clearToken = () => { if (typeof localStorage !== "undefined") localStorage.removeItem(TOKEN_KEY); };

export async function authenticatedFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (!(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const token = storedToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(url, { ...init, headers });
  if (response.status === 401) {
    clearToken();
    if (typeof window !== "undefined") window.dispatchEvent(new Event("tms-auth-expired"));
  }
  return response;
}

export async function apiRequest<T>(url: string, init: RequestInit = {}): Promise<T> {
  const response = await authenticatedFetch(url, init);
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.error?.message ?? `请求失败（${response.status}）`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export async function downloadAuthenticatedFile(url: string, fileName: string): Promise<void> {
  const response = await authenticatedFetch(url);
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.error?.message ?? `下载失败（${response.status}）`);
  }
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = objectUrl;
    anchor.download = fileName;
    anchor.click();
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}
export const login = (login_name: string, password: string) => apiRequest<LoginResult>("/api/v1/auth/login", { method: "POST", body: JSON.stringify({ login_name, password }) });
export const register = (values: { login_name: string; display_name: string; password: string; email?: string; department_code?: string }) => apiRequest<UserRecord>("/api/v1/auth/register", { method: "POST", body: JSON.stringify(values) });
export const getMe = () => apiRequest<CurrentUser>("/api/v1/auth/me");
export const logout = () => apiRequest<void>("/api/v1/auth/logout", { method: "POST" });
export const getUsers = () => apiRequest<UserRecord[]>("/api/v1/auth/users");
export const getRoles = () => apiRequest<{ role_code: string; role_name: string }[]>("/api/v1/auth/roles");
export const updateUser = (userId: number, values: { status: string; role_codes: string[]; department_code?: string }) => apiRequest<UserRecord>(`/api/v1/auth/users/${userId}`, { method: "PUT", body: JSON.stringify(values) });
