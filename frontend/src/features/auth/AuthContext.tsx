import { App as AntApp } from "antd";
import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { clearToken, CurrentUser, getMe, login as loginApi, logout as logoutApi, type PermissionCode, saveToken } from "../../api/auth";

type AuthContextValue = { user: CurrentUser | null; loading: boolean; login: (loginName: string, password: string) => Promise<void>; logout: () => Promise<void>; can: (permission: PermissionCode) => boolean };
const AuthContext = createContext<AuthContextValue | null>(null);
export function AuthProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const { message } = AntApp.useApp(); const [user, setUser] = useState<CurrentUser | null>(null); const [loading, setLoading] = useState(true);
  const clearQueryState = useCallback(async () => {
    await queryClient.cancelQueries();
    queryClient.clear();
  }, [queryClient]);
  const reset = useCallback(async () => {
    clearToken();
    setUser(null);
    setLoading(false);
    await clearQueryState();
  }, [clearQueryState]);
  useEffect(() => {
    const expired = () => { void reset(); };
    window.addEventListener("tms-auth-expired", expired);
    getMe().then(setUser).catch(reset).finally(() => setLoading(false));
    return () => window.removeEventListener("tms-auth-expired", expired);
  }, [reset]);
  const login = useCallback(async (name: string, password: string) => {
    const result = await loginApi(name, password);
    await clearQueryState();
    saveToken(result.access_token);
    setUser(result.user);
    message.success(`欢迎回来，${result.user.display_name}`);
  }, [clearQueryState, message]);
  const logout = useCallback(async () => { try { await logoutApi(); } finally { await reset(); } }, [reset]);
  const value = useMemo<AuthContextValue>(() => ({ user, loading, login, logout, can: (permission) => Boolean(user?.permissions.includes(permission)) }), [user, loading, login, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
export function useAuth() { const value = useContext(AuthContext); if (!value) throw new Error("useAuth must be used inside AuthProvider"); return value; }
