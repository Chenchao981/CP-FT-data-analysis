import { describe, expect, it } from "vitest";

import { formatUtcDateTime, parseUtcTimestamp } from "./dateTime";

describe("UTC date-time formatting", () => {
  it("treats SQL Server timestamps without an offset as UTC", () => {
    expect(parseUtcTimestamp("2026-08-28T00:10:30.062").toISOString())
      .toBe("2026-08-28T00:10:30.062Z");
  });

  it("keeps explicit offsets and displays business time in Asia/Shanghai", () => {
    expect(parseUtcTimestamp("2026-08-28T08:10:30+08:00").toISOString())
      .toBe("2026-08-28T00:10:30.000Z");
    expect(formatUtcDateTime("2026-08-28T00:10:30Z")).toContain("08:10:30");
  });

  it("renders missing or invalid timestamps as an em dash", () => {
    expect(formatUtcDateTime(null)).toBe("—");
    expect(formatUtcDateTime("not-a-date")).toBe("—");
  });
});
