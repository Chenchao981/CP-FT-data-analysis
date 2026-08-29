// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiRequest } from "./auth";

describe("authenticated request", () => {
  const removeItem = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("localStorage", {
      getItem: vi.fn(() => "expired-token"),
      setItem: vi.fn(),
      removeItem,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    removeItem.mockReset();
  });

  it("clears the session and emits one expiry event on 401", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ error: { message: "登录已过期" } }), { status: 401 }),
    );
    const expired = vi.fn();
    window.addEventListener("tms-auth-expired", expired, { once: true });

    await expect(apiRequest("/api/v1/protected")).rejects.toThrow("登录已过期");

    expect(removeItem).toHaveBeenCalledWith("tms_access_token");
    expect(expired).toHaveBeenCalledTimes(1);
  });
});
