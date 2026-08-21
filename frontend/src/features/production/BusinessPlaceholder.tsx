import { ApartmentOutlined, ExperimentOutlined } from "@ant-design/icons";
import { Card, Result, Tag, Typography } from "antd";

export function BusinessPlaceholder({ kind }: { kind: "ENGINEERING" | "FT" }) {
  const engineering = kind === "ENGINEERING";
  return <div className="workbench"><div className="page-heading"><div><Typography.Title level={2}>{engineering ? "工程数据" : "FT数据"}</Typography.Title><Typography.Text type="secondary">{engineering ? "工程试验与验证数据独立于量产业务管理。" : "量产FT数据将沿用FT专用清洗程序和产品型号主线。"}</Typography.Text></div><Tag>{engineering ? "工程" : "量产 / FT"}</Tag></div><Card className="panel-card"><Result icon={engineering ? <ApartmentOutlined /> : <ExperimentOutlined />} title={engineering ? "工程数据模块" : "FT数据模块"} subTitle={engineering ? "业务入口已独立，后续接入工程数据工作流。" : "下一阶段接入日月新FT上传和现有清洗程序。"} /></Card></div>;
}
