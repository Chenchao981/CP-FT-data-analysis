import { Card, Space, Tag, Typography } from "antd";
import type { ReactNode } from "react";

export type AnalysisResultScope = "FORMAL" | "PERSONAL";

interface AnalysisResultFrameProps {
  title: ReactNode;
  scope: AnalysisResultScope;
  children: ReactNode;
  description?: ReactNode;
  extra?: ReactNode;
  className?: string;
  loading?: boolean;
}

/**
 * Shared presentation contract for formal analysis and personal-tool results.
 * Scope is always visible so personal history cannot be mistaken for official KPIs.
 */
export function AnalysisResultFrame({
  title,
  scope,
  children,
  description,
  extra,
  className,
  loading,
}: AnalysisResultFrameProps) {
  return <Card
    className={["analysis-result-frame", className].filter(Boolean).join(" ")}
    loading={loading}
    title={<Space wrap><span>{title}</span><Tag color={scope === "FORMAL" ? "blue" : "cyan"}>{scope === "FORMAL" ? "正式数据" : "个人分析结果"}</Tag></Space>}
    extra={extra}
  >
    {description ? <Typography.Paragraph type="secondary" className="analysis-result-description">{description}</Typography.Paragraph> : null}
    {children}
  </Card>;
}
