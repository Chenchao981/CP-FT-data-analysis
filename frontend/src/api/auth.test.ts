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

  it("preserves structured API error semantics for page recovery actions", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      error: {
        code: "QUICK_SOURCE_CHANGED",
        message: "请重新预览",
        details: [{ path: "body.source_manifest_sha256", message: "manifest 不匹配", type: "value_error" }],
        retryable: true,
        recommended_action: "REFRESH_MANIFEST",
      },
    }), { status: 409 }));

    await expect(apiRequest("/api/v1/test")).rejects.toMatchObject({
      name: "ApiError",
      httpStatus: 409,
      code: "QUICK_SOURCE_CHANGED",
      retryable: true,
      recommendedAction: "REFRESH_MANIFEST",
      fieldErrors: { "body.source_manifest_sha256": ["manifest 不匹配"] },
    });
  });
});
