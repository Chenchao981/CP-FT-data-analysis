import { FileSearchOutlined, InboxOutlined, SafetyCertificateOutlined } from "@ant-design/icons";
import { useMutation } from "@tanstack/react-query";
import { Alert, Button, Card, Col, Descriptions, Progress, Row, Space, Statistic, Tag, Typography, Upload, message } from "antd";
import type { UploadFile } from "antd";
import { useState } from "react";

import { inspectHuaHongFile } from "../../api/cleaners";

export function HuaHongInspector() {
  const [selected, setSelected] = useState<UploadFile>();
  const [messageApi, contextHolder] = message.useMessage();
  const inspection = useMutation({
    mutationFn: inspectHuaHongFile,
    onError: (error) => messageApi.error(error.message),
  });
  const result = inspection.data;

  return (
    <div className="workbench">
      {contextHolder}
      <div className="page-heading">
        <div>
          <Typography.Title level={2}>华虹 CP 样本检查</Typography.Title>
          <Typography.Text type="secondary">检查文件身份、参数Schema、Die数量和Bin结果，不修改原始测试值。</Typography.Text>
        </div>
        <Tag color="cyan" icon={<SafetyCertificateOutlined />}>严格格式合同</Tag>
      </div>

      <Row gutter={[20, 20]}>
        <Col xs={24} xl={9}>
          <Card title="选择单片TXT" className="panel-card inspector-upload-card">
            <Space direction="vertical" size={18} className="full-width">
              <Upload.Dragger
                accept=".txt,.TXT"
                maxCount={1}
                fileList={selected ? [selected] : []}
                beforeUpload={(file) => {
                  setSelected({
                    uid: file.uid,
                    name: file.name,
                    size: file.size,
                    type: file.type,
                    status: "done",
                    originFileObj: file,
                  });
                  inspection.reset();
                  return false;
                }}
                onRemove={() => {
                  setSelected(undefined);
                  inspection.reset();
                }}
              >
                <p className="ant-upload-drag-icon"><InboxOutlined /></p>
                <p className="ant-upload-text">点击或拖入华虹TXT文件</p>
                <p className="ant-upload-hint">只读取当前文件；原始数据不会进入Git仓库。</p>
              </Upload.Dragger>
              <Button
                type="primary"
                block
                icon={<FileSearchOutlined />}
                disabled={!selected?.originFileObj}
                loading={inspection.isPending}
                onClick={() => selected?.originFileObj && inspection.mutate(selected.originFileObj)}
              >
                检查格式和数据
              </Button>
              {inspection.isError && <Alert type="error" showIcon message="文件被阻断" description={inspection.error.message} />}
            </Space>
          </Card>
        </Col>

        <Col xs={24} xl={15}>
          <Card title="检查结果" className="panel-card">
            {!result && !inspection.isPending && (
              <div className="empty-state"><FileSearchOutlined /><Typography.Text type="secondary">检查后显示可追溯的数据摘要。</Typography.Text></div>
            )}
            {result && (
              <Space direction="vertical" size={22} className="full-width">
                <Alert type="success" showIcon message="格式和基础数据质量检查通过" description={`${result.profile_code} / ${result.schema.schema_id}`} />
                <Row gutter={[14, 14]}>
                  <Col xs={12} md={6}><Statistic title="Die数量" value={result.quality.row_count} /></Col>
                  <Col xs={12} md={6}><Statistic title="Pass数量" value={result.quality.pass_count} /></Col>
                  <Col xs={12} md={6}><Statistic title="参数数量" value={result.schema.parameter_count} /></Col>
                  <Col xs={12} md={6}><Statistic title="Pass Bin" value={result.quality.pass_bin} /></Col>
                </Row>
                <div>
                  <Typography.Text type="secondary">Wafer良率</Typography.Text>
                  <Progress percent={Number((result.quality.yield_rate * 100).toFixed(2))} status="success" />
                </div>
                <Descriptions bordered size="small" column={1}>
                  <Descriptions.Item label="业务Lot">{result.identity.business_lot_id}</Descriptions.Item>
                  <Descriptions.Item label="Wafer">{result.identity.wafer_number}</Descriptions.Item>
                  <Descriptions.Item label="源Run">{result.identity.lot_number}</Descriptions.Item>
                  <Descriptions.Item label="测试程序">{result.identity.program_name}</Descriptions.Item>
                  <Descriptions.Item label="文件SHA256"><Typography.Text code copyable>{result.source_file.sha256}</Typography.Text></Descriptions.Item>
                </Descriptions>
                <div>
                  <Typography.Text strong>Bin分布</Typography.Text>
                  <div className="tag-list">
                    {Object.entries(result.quality.bin_counts).map(([bin, count]) => <Tag key={bin} color={Number(bin) === result.quality.pass_bin ? "green" : "red"}>Bin {bin}: {count}</Tag>)}
                  </div>
                </div>
              </Space>
            )}
          </Card>
        </Col>
      </Row>
    </div>
  );
}
