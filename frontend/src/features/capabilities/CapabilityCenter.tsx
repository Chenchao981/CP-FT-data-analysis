import { ApartmentOutlined, ExperimentOutlined, SafetyCertificateOutlined, ToolOutlined } from "@ant-design/icons";
import { Alert, Card, Col, List, Row, Space, Tag, Typography } from "antd";
import { customCapabilities, generalCapabilities } from "./capabilityCatalog";

const routeLabel = {
  FORMAL_IMPORT: { color: "success", text: "正式入库已开放" },
  PENDING_FORMAL_IMPORT: { color: "default", text: "正式入库待验收" },
} as const;

export function CapabilityCenter() {
  return <div className="workbench production-workbench">
    <div className="page-heading">
      <div>
        <Typography.Text type="secondary">能力治理 / 固定业务边界</Typography.Text>
        <Typography.Title level={2}>能力中心</Typography.Title>
        <Typography.Text type="secondary">通用正式入库、定制工具和快速分析分别建设、分别验收。</Typography.Text>
      </div>
    </div>

    <Alert
      showIcon
      type="info"
      message="入口只代表已经完成端到端验收的能力"
      description="旧项目中能够清洗，不等于已经接入 TMS 正式数据库。未完成 Factory、Release、Output Adapter 和真实 SQL 对账的厂家不会出现在正式上传下拉框。"
    />

    <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
      <Col xs={24} xl={14}>
        <Card title={<Space><SafetyCertificateOutlined />通用数据能力</Space>}>
          <List
            dataSource={generalCapabilities}
            renderItem={(item) => {
              const status = routeLabel[item.route];
              return <List.Item extra={<Tag color={status.color}>{status.text}</Tag>}>
                <List.Item.Meta
                  avatar={item.stage === "CP" ? <ExperimentOutlined /> : <ApartmentOutlined />}
                  title={<Space><Typography.Text strong>{item.label}</Typography.Text><Tag>{item.stage}</Tag></Space>}
                  description={item.scope}
                />
              </List.Item>;
            }}
          />
        </Card>
      </Col>
      <Col xs={24} xl={10}>
        <Card title={<Space><ToolOutlined />定制工具</Space>}>
          <List
            dataSource={customCapabilities}
            renderItem={(item) => <List.Item>
              <List.Item.Meta
                title={<Space wrap><Typography.Text strong>{item.name}</Typography.Text><Tag color="purple">定制</Tag></Space>}
                description={<Space direction="vertical" size={2}>
                  <Typography.Text>{item.ownerScope}</Typography.Text>
                  <Typography.Text type="secondary">{item.currentRuntime}</Typography.Text>
                  <Typography.Text type="secondary">边界：{item.boundary}</Typography.Text>
                </Space>}
              />
            </List.Item>}
          />
        </Card>
      </Col>
    </Row>

    <Alert
      showIcon
      type="warning"
      style={{ marginTop: 16 }}
      message="Lot 与 Spec 发布门禁"
      description="当前已批准格式应自动取得 Lot。若 Lot 缺失，正式发布必须停止并等待人工补录；Spec 按 Lot 绑定，同 Lot 的不同测试 Run 若规格不同则继续隔离，不用目录名或 unknown 代替业务批次。"
    />
  </div>;
}
