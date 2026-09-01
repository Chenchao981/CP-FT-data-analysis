// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { App as AntApp } from "antd";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useAuth } from "./AuthContext";
import { LoginPage } from "./LoginPage";

vi.mock("./AuthContext", () => ({ useAuth: vi.fn() }));
vi.mock("../../api/auth", () => ({ register: vi.fn() }));

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => undefined,
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false,
  }),
});

describe("LoginPage", () => {
  afterEach(() => { cleanup(); vi.clearAllMocks(); });

  it("presents the branded manufacturing data story", () => {
    vi.mocked(useAuth).mockReturnValue({ login: vi.fn() } as never);
    render(<AntApp><LoginPage /></AntApp>);

    expect(screen.getByText("欢迎进入 TMS")).toBeInTheDocument();
    expect(screen.getByText(/让每一颗芯片的测试数据/)).toBeInTheDocument();
    expect(screen.getByText("版本化 Cleaner")).toBeInTheDocument();
    expect(screen.getByText("Canonical 数据链")).toBeInTheDocument();
  });

  it("submits only the explicit login fields", async () => {
    const login = vi.fn().mockResolvedValue(undefined);
    vi.mocked(useAuth).mockReturnValue({ login } as never);
    render(<AntApp><LoginPage /></AntApp>);

    fireEvent.change(screen.getByPlaceholderText("请输入登录名"), { target: { value: "tester" } });
    fireEvent.change(screen.getByPlaceholderText("请输入密码"), { target: { value: "password-123" } });
    fireEvent.click(screen.getByRole("button", { name: /登录系统/ }));

    await waitFor(() => expect(login).toHaveBeenCalledWith("tester", "password-123"));
  });
});
