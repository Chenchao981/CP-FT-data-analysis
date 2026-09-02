import type { ReactNode } from "react";

export interface MetricStripItem {
  label: string;
  value: ReactNode;
  tone?: "default" | "primary" | "success" | "warning" | "danger";
  note?: string;
}

export function MetricStrip({
  items,
  ariaLabel = "关键指标",
}: {
  items: readonly MetricStripItem[];
  ariaLabel?: string;
}) {
  return <div className="metric-strip" role="list" aria-label={ariaLabel}>
    {items.map((item) => <div className={`metric-strip-item metric-strip-${item.tone ?? "default"}`} role="listitem" key={item.label}>
      <span className="metric-strip-label">{item.label}</span>
      <strong className="metric-strip-value">{item.value}</strong>
      {item.note && <small>{item.note}</small>}
    </div>)}
  </div>;
}
