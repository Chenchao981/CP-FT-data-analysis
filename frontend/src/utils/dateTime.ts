const hasExplicitTimeZone = /(?:Z|[+-]\d{2}:?\d{2})$/i;
const shanghaiOffsetMs = 8 * 60 * 60 * 1000;
const localDateTimePattern = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/;

export function parseUtcTimestamp(value: string): Date {
  return new Date(hasExplicitTimeZone.test(value) ? value : `${value}Z`);
}

export function formatUtcDateTime(value?: string | null): string {
  if (!value) return "—";
  const parsed = parseUtcTimestamp(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return parsed.toLocaleString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" });
}

export function formatUtcDate(value?: string | null): string {
  if (!value) return "—";
  const parsed = parseUtcTimestamp(value);
  return Number.isNaN(parsed.getTime()) ? "—" : parsed.toISOString().slice(0, 10);
}

export function utcToShanghaiLocalInput(value?: string | null): string | undefined {
  if (!value) return undefined;
  const parsed = parseUtcTimestamp(value);
  if (Number.isNaN(parsed.getTime())) return undefined;
  return new Date(parsed.getTime() + shanghaiOffsetMs).toISOString().slice(0, 16);
}

export function formatShanghaiDate(value?: string | null): string {
  return utcToShanghaiLocalInput(value)?.slice(0, 10) ?? "—";
}

export function shanghaiLocalInputToUtc(value?: string | null): string | undefined {
  const normalized = value?.trim();
  if (!normalized) return undefined;
  const match = localDateTimePattern.exec(normalized);
  if (!match) return undefined;
  const withSeconds = normalized.length === 16 ? `${normalized}:00` : normalized;
  const parsed = new Date(`${withSeconds}+08:00`);
  if (Number.isNaN(parsed.getTime())) return undefined;
  const roundTrip = new Date(parsed.getTime() + shanghaiOffsetMs)
    .toISOString()
    .slice(0, 19);
  return roundTrip === withSeconds ? parsed.toISOString() : undefined;
}

export function recentShanghaiDayRange(days: number, now = new Date()): { from: string; to: string } {
  const normalizedDays = Number.isFinite(days) ? Math.max(1, Math.floor(days)) : 1;
  const shanghaiNow = new Date(now.getTime() + shanghaiOffsetMs);
  const endDay = new Date(Date.UTC(
    shanghaiNow.getUTCFullYear(),
    shanghaiNow.getUTCMonth(),
    shanghaiNow.getUTCDate() + 1,
  ));
  const startDay = new Date(endDay.getTime() - normalizedDays * 24 * 60 * 60 * 1000);
  return {
    from: startDay.toISOString().slice(0, 16),
    to: endDay.toISOString().slice(0, 16),
  };
}
