import { describe, expect, it } from "vitest";

import {
  formatShanghaiDate,
  formatUtcDate,
  formatUtcDateTime,
  parseUtcTimestamp,
  recentShanghaiDayRange,
  shanghaiLocalInputToUtc,
  utcToShanghaiLocalInput,
} from "./dateTime";

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
    expect(formatUtcDate("not-a-date")).toBe("—");
  });

  it("keeps UTC trend dates as dates instead of shifting them to 08:00 Shanghai", () => {
    expect(formatUtcDate("2026-08-29T00:00:00Z")).toBe("2026-08-29");
    expect(formatShanghaiDate("2026-08-28T16:00:00Z")).toBe("2026-08-29");
  });

  it("round-trips business datetime-local values with the explicit Shanghai offset", () => {
    expect(shanghaiLocalInputToUtc("2026-08-29T08:30")).toBe("2026-08-29T00:30:00.000Z");
    expect(shanghaiLocalInputToUtc("2026-08-29T08:30:45")).toBe("2026-08-29T00:30:45.000Z");
    expect(utcToShanghaiLocalInput("2026-08-29T00:30:00Z")).toBe("2026-08-29T08:30");
    expect(shanghaiLocalInputToUtc("invalid")).toBeUndefined();
    expect(shanghaiLocalInputToUtc("2026-02-30T08:30")).toBeUndefined();
    expect(shanghaiLocalInputToUtc("2026-08-29T24:00")).toBeUndefined();
  });

  it("creates an exclusive next-midnight range in Shanghai business time", () => {
    expect(recentShanghaiDayRange(7, new Date("2026-08-29T02:00:00Z"))).toEqual({
      from: "2026-08-23T00:00",
      to: "2026-08-30T00:00",
    });
    expect(recentShanghaiDayRange(Number.NaN, new Date("2026-08-29T02:00:00Z"))).toEqual({
      from: "2026-08-29T00:00",
      to: "2026-08-30T00:00",
    });
  });
});
