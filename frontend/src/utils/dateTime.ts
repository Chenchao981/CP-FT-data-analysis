const hasExplicitTimeZone = /(?:Z|[+-]\d{2}:?\d{2})$/i;

export function parseUtcTimestamp(value: string): Date {
  return new Date(hasExplicitTimeZone.test(value) ? value : `${value}Z`);
}

export function formatUtcDateTime(value?: string | null): string {
  if (!value) return "—";
  const parsed = parseUtcTimestamp(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return parsed.toLocaleString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" });
}
