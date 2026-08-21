import { App as AntApp } from "antd";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { clearToken, CurrentUser, getMe, login as loginApi, logout as logoutApi, saveToken, storedToken } from "../../api/auth";

type AuthContextValue = { user: CurrentUser | null; loading: boolean; login: (loginName: string, password: string) => Promise<void>; logout: () => Promise<void>; can: (permission: string) => boolean };
const AuthContext = createContext<AuthContextValue | null>(null);
export function AuthProvider({ children }: { children: React.ReactNode }) {
  const { message } = AntApp.useApp(); const [user, setUser] = useState<CurrentUser | null>(null); const [loading, setLoading] = useState(true);
  const reset = useCallback(() => { clearToken(); setUser(null); setLoading(false); }, []);
  useEffect(() => { const expired = () => reset(); window.addEventListener("tms-auth-expired", expired); if (!storedToken()) setLoading(false); else getMe().then(setUser).catch(reset).finally(() => setLoading(false)); return () => window.removeEventListener("tms-auth-expired", expired); }, [reset]);
  const login = useCallback(async (name: string, password: string) => { const result = await loginApi(name, password); saveToken(result.access_token); setUser(result.user); message.success(`欢迎回来，${result.user.display_name}`); }, [message]);
  const logout = useCallback(async () => { try { await logoutApi(); } finally { reset(); } }, [reset]);
  const value = useMemo<AuthContextValue>(() => ({ user, loading, login, logout, can: (permission) => Boolean(user?.permissions.includes(permission)) }), [user, loading, login, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
export function useAuth() { const value = useContext(AuthContext); if (!value) throw new Error("useAuth must be used inside AuthProvider"); return value; }
