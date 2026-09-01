// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider, useAuth } from "./AuthContext";
import { getMe, login, logout, saveToken } from "../../api/auth";
import {
  saveLocalAgentRunReference,
  saveLocalAgentToken,
  storedLocalAgentRunReference,
  storedLocalAgentToken,
} from "../../api/localAgent";

vi.mock("../../api/auth", () => ({
  clearToken: vi.fn(),
  getMe: vi.fn(),
  login: vi.fn(),
  logout: vi.fn(),
  saveToken: vi.fn(),
}));

const developmentUser = {
  user_id: 1,
  login_name: "development-admin",
  display_name: "开发管理员",
  department_code: null,
  roles: ["SYSTEM_ADMIN"],
  permissions: ["DATASET_READ"],
};
const productionReader = {
  ...developmentUser,
  user_id: 2,
  login_name: "production-reader",
  display_name: "量产查询员",
  roles: ["DATA_READER"],
};

function CurrentIdentity() {
  const { user, loading, login: signIn, logout: signOut } = useAuth();
  if (loading) return <div>loading</div>;
  return <div>
    <div>{user?.login_name ?? "anonymous"}</div>
    <button onClick={() => void signIn("production-reader", "password")}>switch identity</button>
    <button onClick={() => void signOut()}>sign out</button>
  </div>;
}

function renderAuth(queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  render(
    <AntApp>
      <QueryClientProvider client={queryClient}>
        <AuthProvider><CurrentIdentity /></AuthProvider>
      </QueryClientProvider>
    </AntApp>,
  );
  return queryClient;
}

describe("AuthProvider", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
    localStorage.clear();
    vi.mocked(logout).mockResolvedValue(undefined);
    vi.mocked(login).mockResolvedValue({
      access_token: "new-token",
      token_type: "bearer",
      expires_at_utc: "2026-08-30T10:00:00Z",
      user: productionReader,
    });
  });

  afterEach(() => cleanup());

  it("loads the backend development principal even when no browser token exists", async () => {
    vi.mocked(getMe).mockResolvedValue(developmentUser);
    renderAuth();
    expect(await screen.findByText("development-admin")).toBeInTheDocument();
    expect(getMe).toHaveBeenCalledOnce();
  });

  it("cancels and clears cached owner data before accepting a new login identity", async () => {
    vi.mocked(getMe).mockResolvedValue(developmentUser);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    queryClient.setQueryData(["stage", "ENGINEERING", "CP"], { owner: "development-admin" });
    const cancelQueries = vi.spyOn(queryClient, "cancelQueries");
    const clear = vi.spyOn(queryClient, "clear");
    renderAuth(queryClient);
    await screen.findByText("development-admin");

    fireEvent.click(screen.getByRole("button", { name: "switch identity" }));

    expect(await screen.findByText("production-reader")).toBeInTheDocument();
    expect(cancelQueries).toHaveBeenCalledOnce();
    expect(clear).toHaveBeenCalledOnce();
    expect(queryClient.getQueryData(["stage", "ENGINEERING", "CP"])).toBeUndefined();
    expect(saveToken).toHaveBeenCalledWith("new-token");
  });

  it("cancels and clears cached data when the user logs out", async () => {
    vi.mocked(getMe).mockResolvedValue(developmentUser);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    queryClient.setQueryData(["datasets", "current"], { owner: "development-admin" });
    const cancelQueries = vi.spyOn(queryClient, "cancelQueries");
    const clear = vi.spyOn(queryClient, "clear");
    renderAuth(queryClient);
    await screen.findByText("development-admin");

    fireEvent.click(screen.getByRole("button", { name: "sign out" }));

    expect(await screen.findByText("anonymous")).toBeInTheDocument();
    await waitFor(() => expect(queryClient.getQueryData(["datasets", "current"])).toBeUndefined());
    expect(cancelQueries).toHaveBeenCalledOnce();
    expect(clear).toHaveBeenCalledOnce();
    expect(logout).toHaveBeenCalledOnce();
  });

  it("does not let user B recover user A's Local Agent run after A logs out", async () => {
    vi.mocked(getMe).mockResolvedValue(developmentUser);
    saveLocalAgentToken("user-a-agent-token");
    saveLocalAgentRunReference(
      "123e4567-e89b-42d3-a456-426614174000",
      91,
    );
    renderAuth();
    await screen.findByText("development-admin");

    fireEvent.click(screen.getByRole("button", { name: "sign out" }));
    await screen.findByText("anonymous");
    fireEvent.click(screen.getByRole("button", { name: "switch identity" }));
    await screen.findByText("production-reader");

    expect(storedLocalAgentToken()).toBeNull();
    expect(storedLocalAgentRunReference()).toBeNull();
  });

  it("cancels and clears cached data when authentication expires", async () => {
    vi.mocked(getMe).mockResolvedValue(developmentUser);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    queryClient.setQueryData(["analytics", "engineering"], { owner: "development-admin" });
    const cancelQueries = vi.spyOn(queryClient, "cancelQueries");
    const clear = vi.spyOn(queryClient, "clear");
    renderAuth(queryClient);
    await screen.findByText("development-admin");
    saveLocalAgentToken("expired-user-agent-token");
    saveLocalAgentRunReference(
      "123e4567-e89b-42d3-a456-426614174000",
    );

    window.dispatchEvent(new Event("tms-auth-expired"));

    expect(await screen.findByText("anonymous")).toBeInTheDocument();
    await waitFor(() => expect(queryClient.getQueryData(["analytics", "engineering"])).toBeUndefined());
    expect(cancelQueries).toHaveBeenCalledOnce();
    expect(clear).toHaveBeenCalledOnce();
    expect(storedLocalAgentToken()).toBeNull();
    expect(storedLocalAgentRunReference()).toBeNull();
  });

});
