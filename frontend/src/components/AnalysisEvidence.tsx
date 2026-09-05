import { Descriptions } from "antd";

export interface AnalysisEvidenceProps {
  method?: string | null;
  source?: string | null;
  inputCount?: number | null;
  includedCount?: number | null;
  excludedCount?: number | null;
  missingCount?: number | null;
}

/** Missing evidence stays unknown; totals refer to the explicitly supplied population. */
export function AnalysisEvidence(props: AnalysisEvidenceProps) {
  const count = (value?: number | null) => value == null ? "未提供" : value.toLocaleString("zh-CN");
  return <Descriptions size="small" column={{ xs: 1, sm: 2 }}>
    <Descriptions.Item label="计算方法">{props.method || "见结果报告"}</Descriptions.Item>
    <Descriptions.Item label="数据来源">{props.source || "见来源记录"}</Descriptions.Item>
    <Descriptions.Item label="输入数量">{count(props.inputCount)}</Descriptions.Item>
    <Descriptions.Item label="纳入数量">{count(props.includedCount)}</Descriptions.Item>
    <Descriptions.Item label="排除数量">{count(props.excludedCount)}</Descriptions.Item>
    <Descriptions.Item label="缺失测量数量">{count(props.missingCount)}</Descriptions.Item>
  </Descriptions>;
}
