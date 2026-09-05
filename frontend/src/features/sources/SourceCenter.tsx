import { CloudServerOutlined, ReloadOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Empty, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";

import { getSourceCenterSnapshot } from "../../api/sourceCenter";
import type { FormalSourceRoot, StageUploadRow } from "../../api/stageData";
import { formatUtcDateTime } from "../../utils/dateTime";
import { factoryNames } from "../capabilities/capabilityCatalog";
import { FtpSourcesPanel } from "./FtpSourcesPanel";

interface SourceCenterProps {
  canManageSources?: boolean;
  onNavigate: (path: string) => void;
  onOpenJob: (jobId: number) => void;
}

const statusColor: Record<string, string> = { PROCESSED: "success", PROCESSING: "processing", QUEUED: "gold", FAILED: "error", NEEDS_INPUT: "warning" };
const displaySourceName = (value: string) => value.replace(/工程|量产/g, "").replace(/\s{2,}/g, " ").trim();

export function SourceCenter({ onNavigate, onOpenJob, canManageSources = false }: SourceCenterProps) {
  const snapshot = useQuery({ queryKey: ["source-center", "snapshot"], queryFn: getSourceCenterSnapshot });
  const rootColumns: ColumnsType<FormalSourceRoot> = [
    { title: "数据源", dataIndex: "name", fixed: "left", width: 220, render: displaySourceName },
    { title: "阶段", dataIndex: "test_stage", width: 80, render: (value) => <Tag color={value === "CP" ? "blue" : "purple"}>{value}</Tag> },
    { title: "厂家", dataIndex: "factory_code", width: 120, render: (value) => factoryNames[String(value).toLowerCase()] ?? value },
    { title: "文件类型", dataIndex: "allowed_suffixes", width: 200, render: (value: string[]) => value.join("、") },
    { title: "连接状态", dataIndex: "available", width: 110, render: (value) => <Tag color={value ? "success" : "error"}>{value ? "可访问" : "不可访问"}</Tag> },
    { title: "入口", key: "entry", width: 120, render: (_, row) => <Button type="link" size="small" onClick={() => onNavigate(row.test_stage === "CP" ? "/cp" : "/ft")}>浏览并入库</Button> },
  ];
  const importColumns: ColumnsType<StageUploadRow> = [
    { title: "批次", dataIndex: "import_batch_id", width: 90, render: (value) => `#${value}` },
    { title: "源文件", dataIndex: "original_file_name", width: 300, ellipsis: true },
    { title: "阶段 / 厂家", key: "scope", width: 180, render: (_, row) => `${row.factory_code}` },
    { title: "指纹判重", dataIndex: "is_duplicate_receipt", width: 110, render: (value) => <Tag color={value ? "warning" : "success"}>{value ? "重复" : "新文件"}</Tag> },
    { title: "状态", dataIndex: "status", width: 110, render: (value) => <Tag color={statusColor[value]}>{value}</Tag> },
    { title: "接收时间", dataIndex: "upload_time_utc", width: 180, render: formatUtcDateTime },
    { title: "任务", dataIndex: "latest_job_id", width: 110, render: (value) => value ? <Button type="link" size="small" onClick={() => onOpenJob(value)}>Job #{value}</Button> : "—" },
  ];

  return <div className="workbench production-workbench">
    <div className="page-heading"><Typography.Title level={2}>数据源中心</Typography.Title><Button icon={<ReloadOutlined />} loading={snapshot.isFetching} onClick={() => void snapshot.refetch()}>刷新</Button></div>
    {snapshot.isError && <Alert type="error" showIcon message="数据源中心加载失败" description={snapshot.error.message} className="review-alert" />}
    {snapshot.data?.unavailableQueries ? <Alert type="info" showIcon message="部分数据源无查看权限" className="review-alert" /> : null}
    <FtpSourcesPanel canManage={canManageSources} onOpenJob={onOpenJob} />
    <Card title={<Space><CloudServerOutlined />已配置正式数据源</Space>} className="production-table-card" style={{ marginBottom: 18 }}>
      <Table rowKey="code" columns={rootColumns} dataSource={snapshot.data?.roots ?? []} loading={snapshot.isLoading} pagination={false} locale={{ emptyText: <Empty description="当前账号没有可见的正式 FTP/NAS 数据源" /> }} scroll={{ x: 950 }} />
    </Card>
    <Card title="最近服务器采集记录" className="production-table-card">
      <Table rowKey={(row) => `${row.import_batch_id}-${row.receipt_id}`} columns={importColumns} dataSource={snapshot.data?.recentImports ?? []} loading={snapshot.isLoading} pagination={{ pageSize: 15, hideOnSinglePage: true }} locale={{ emptyText: <Empty description="暂无服务器目录正式采集记录" /> }} scroll={{ x: 1200 }} />
    </Card>
  </div>;
}
