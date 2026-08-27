// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { App as AntApp } from "antd";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider, useAuth } from "./AuthContext";
import { getMe } from "../../api/auth";

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

function CurrentIdentity() {
  const { user, loading } = useAuth();
  if (loading) return <div>loading</div>;
  return <div>{user?.login_name ?? "anonymous"}</div>;
}

describe("AuthProvider", () => {
  beforeEach(() => vi.mocked(getMe).mockReset());

  it("loads the backend development principal even when no browser token exists", async () => {
    vi.mocked(getMe).mockResolvedValue(developmentUser);
    render(<AntApp><AuthProvider><CurrentIdentity /></AuthProvider></AntApp>);
    expect(await screen.findByText("development-admin")).toBeInTheDocument();
    expect(getMe).toHaveBeenCalledOnce();
  });

});
