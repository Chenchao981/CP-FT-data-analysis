import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Descriptions, Space, Table, Tag } from "antd";
import { apiRequest } from "../../api/auth";

interface Capability {
  capability_code: string;
  display_name: string;
  test_stage: string;
  use_scopes: string[];
  input_contract_version: string;
  output_contract_version: string;
  release_status: string;
  release: { version: string; sha256: string; release_id: number } | null;
  format_methods: Array<{ method_code: string; display_name: string; extensions: string[]; fail_closed_notes: string }>;
}

export function CleanerCapabilityCatalog() {
  const query = useQuery({ queryKey: ["cleaner-capabilities"], queryFn: () => apiRequest<Capability[]>("/api/v1/contracts/cleaner-capability-status") });
  return <Card title="清洗能力目录" extra={<Button onClick={() => void query.refetch()} loading={query.isFetching}>刷新能力</Button>}>
    {query.isError && <Alert type="error" showIcon message="清洗能力目录加载失败" />}
    <Table<Capability> rowKey="capability_code" loading={query.isLoading} dataSource={query.data ?? []} pagination={false}
      columns={[
        { title: "清洗工具", dataIndex: "display_name" },
        { title: "阶段", dataIndex: "test_stage" },
        { title: "用途", render: (_, row) => <Space>{row.use_scopes.map(scope => <Tag key={scope}>{scope === "FORMAL_IMPORT" ? "正式入库" : "个人分析"}</Tag>)}</Space> },
        { title: "支持格式", render: (_, row) => row.format_methods.map(item => item.display_name).join("、") },
        { title: "正式包登记", render: (_, row) => row.release ? <Tag color="green">{row.release.version}</Tag> : row.release_status === "PERSONAL_CONTRACT" ? "按个人工具登记" : "未登记可用正式包" },
      ]}
      expandable={{ expandedRowRender: row => <Descriptions column={1} size="small">
        <Descriptions.Item label="输入合同">{row.input_contract_version}</Descriptions.Item>
        <Descriptions.Item label="输出合同">{row.output_contract_version}</Descriptions.Item>
        {row.release && <Descriptions.Item label="正式包 SHA-256">{row.release.sha256}</Descriptions.Item>}
        {row.format_methods.map(item => <Descriptions.Item key={item.method_code} label={item.display_name}>{item.extensions.join(" / ")}；{item.fail_closed_notes}</Descriptions.Item>)}
      </Descriptions> }}
    />
  </Card>;
}
