// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createDataDomain,
  grantDataDomain,
  listAdminDataDomains,
  listGrantableUsers,
  listMyDataDomains,
  revokeDataDomain,
  updateDataDomain,
} from "./dataDomains";

describe("data domain API", () => {
  afterEach(() => vi.restoreAllMocks());

  it("keeps self-service and control-plane endpoints separate", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => (
      new Response(JSON.stringify([]), { status: 200, headers: { "Content-Type": "application/json" } })
    ));

    await listMyDataDomains();
    await listAdminDataDomains();
    await listGrantableUsers();

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/v1/data-domains",
      "/api/v1/admin/data-domains",
      "/api/v1/admin/data-domains/grantable-users",
    ]);
  });

  it("uses explicit create, update, grant and revoke contracts", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (_url, init) => (
      init?.method === "DELETE"
        ? new Response(null, { status: 204 })
        : new Response(JSON.stringify({}), { status: 200, headers: { "Content-Type": "application/json" } })
    ));

    await createDataDomain({ domain_code: "HUAHONG_CP", domain_name: "华虹 CP", test_stage: "CP", active: true });
    await updateDataDomain(11, { domain_name: "华虹 CP", factory_code: "HUAHONG", active: true });
    await grantDataDomain(11, { user_id: 7, expires_at_utc: null, reason: "负责华虹 CP 数据分析" });
    await revokeDataDomain(11, 7);

    expect(fetchMock.mock.calls.map(([url, init]) => [url, init?.method])).toEqual([
      ["/api/v1/admin/data-domains", "POST"],
      ["/api/v1/admin/data-domains/11", "PUT"],
      ["/api/v1/admin/data-domains/11/grants", "POST"],
      ["/api/v1/admin/data-domains/11/grants/7", "DELETE"],
    ]);
    expect(JSON.parse(String(fetchMock.mock.calls[2][1]?.body))).toEqual({
      user_id: 7,
      expires_at_utc: null,
      reason: "负责华虹 CP 数据分析",
    });
  });
});
