import {
  ModalForm,
  ProFormSelect,
  ProFormSwitch,
  ProFormText,
  ProTable,
} from "@ant-design/pro-components";
import type { ProColumns } from "@ant-design/pro-components";
import { App, Button, Popconfirm, Space, Tag, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";

import {
  createDataDomain,
  grantDataDomain,
  listAdminDataDomains,
  listGrantableUsers,
  revokeDataDomain,
  updateDataDomain,
  type CreateDataDomainValues,
  type DataDomain,
  type GrantableUser,
  type UpdateDataDomainValues,
} from "../../api/dataDomains";
import { formatUtcDateTime } from "../../utils/dateTime";

interface GrantFormValues {
  user_id: number;
  expires_at_utc?: string;
  reason: string;
}

export function DataDomainManagement() {
  const { message } = App.useApp();
  const [domains, setDomains] = useState<DataDomain[]>([]);
  const [users, setUsers] = useState<GrantableUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<DataDomain | null>(null);
  const [granting, setGranting] = useState<DataDomain | null>(null);

  const refresh = async () => {
    setLoading(true);
    try {
      const [nextDomains, nextUsers] = await Promise.all([
        listAdminDataDomains(),
        listGrantableUsers(),
      ]);
      setDomains(nextDomains);
      setUsers(nextUsers);
    } catch (error) {
      message.error((error as Error).message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void refresh(); }, []);

  const grantOptions = useMemo(() => users.map((user) => ({
    label: `${user.display_name}（${user.login_name} / ID ${user.user_id}）`,
    value: user.user_id,
  })), [users]);

  const revoke = async (domain: DataDomain, userId: number) => {
    try {
      await revokeDataDomain(domain.data_domain_id, userId);
      message.success("数据域授权已撤销，下次请求立即生效");
      await refresh();
    } catch (error) {
      message.error((error as Error).message);
    }
  };

  const columns: ProColumns<DataDomain>[] = [
    {
      title: "数据域",
      dataIndex: "domain_name",
      render: (_, row) => <><strong>{row.domain_name}</strong><br/><Typography.Text type="secondary">{row.domain_code}</Typography.Text></>,
    },
    { title: "路线", dataIndex: "test_stage", render: (_, row) => <Tag color={row.test_stage === "CP" ? "cyan" : "purple"}>{row.test_stage}</Tag> },
    { title: "厂家", dataIndex: "factory_code", renderText: (value) => value || "—" },
    { title: "状态", dataIndex: "active", render: (_, row) => row.active ? <Tag color="success">启用</Tag> : <Tag>停用</Tag> },
    {
      title: "已授权用户",
      dataIndex: "grants",
      render: (_, row) => row.grants.length ? <Space direction="vertical" size={4}>{row.grants.map((grant) => <Space key={grant.user_id} size={4} wrap>
        <Tag>{grant.display_name}{grant.expires_at_utc ? ` · 至 ${formatUtcDateTime(grant.expires_at_utc)}` : ""}</Tag>
        <Popconfirm
          title={`撤销 ${grant.display_name} 的数据域访问？`}
          description="撤销后，Dataset、分析、导出和 Artifact 下载将在下一次请求立即失败关闭。"
          okText="撤销"
          cancelText="取消"
          onConfirm={() => void revoke(row, grant.user_id)}
        ><Button type="link" danger size="small">撤销</Button></Popconfirm>
      </Space>)}</Space> : <Typography.Text type="secondary">尚未授权</Typography.Text>,
    },
    {
      title: "操作",
      valueType: "option",
      render: (_, row) => [
        <Button key="grant" type="link" onClick={() => setGranting(row)}>授权用户</Button>,
        <Button key="edit" type="link" onClick={() => setEditing(row)}>编辑</Button>,
      ],
    },
  ];

  return <div className="workbench">
    <div className="page-heading"><div>
      <Typography.Title level={2}>数据域授权</Typography.Title>
      <Typography.Text type="secondary">系统采集数据属于数据域；只有这里的显式有效授权决定谁能查看。角色、部门和任务分配都不会隐式授予数据权。</Typography.Text>
    </div></div>
    <ProTable<DataDomain>
      rowKey="data_domain_id"
      search={false}
      loading={loading}
      dataSource={domains}
      columns={columns}
      pagination={{ pageSize: 10 }}
      toolBarRender={() => [
        <Button key="refresh" onClick={() => void refresh()}>刷新</Button>,
        <Button key="create" type="primary" onClick={() => setCreating(true)}>新建数据域</Button>,
      ]}
    />

    <ModalForm<CreateDataDomainValues>
      title="新建数据域"
      open={creating}
      onOpenChange={setCreating}
      initialValues={{ test_stage: "CP", active: true }}
      modalProps={{ destroyOnHidden: true }}
      onFinish={async (values) => {
        try {
          await createDataDomain(values);
          message.success("数据域已创建");
          setCreating(false);
          await refresh();
          return true;
        } catch (error) {
          message.error((error as Error).message);
          return false;
        }
      }}
    >
      <ProFormText name="domain_code" label="数据域编码" placeholder="例如 HUAHONG_CP" rules={[{ required: true }]} />
      <ProFormText name="domain_name" label="数据域名称" placeholder="例如 华虹 CP" rules={[{ required: true }]} />
      <ProFormSelect name="test_stage" label="测试路线" options={[{ label: "CP", value: "CP" }, { label: "FT", value: "FT" }]} rules={[{ required: true }]} />
      <ProFormText name="factory_code" label="厂家编码（可选）" />
      <ProFormSwitch name="active" label="启用" />
    </ModalForm>

    <ModalForm<UpdateDataDomainValues>
      key={editing?.data_domain_id ?? "edit-empty"}
      title={`编辑数据域：${editing?.domain_name ?? ""}`}
      open={Boolean(editing)}
      onOpenChange={(open) => { if (!open) setEditing(null); }}
      initialValues={editing ? { domain_name: editing.domain_name, factory_code: editing.factory_code ?? undefined, active: editing.active } : {}}
      modalProps={{ destroyOnHidden: true }}
      onFinish={async (values) => {
        if (!editing) return false;
        try {
          await updateDataDomain(editing.data_domain_id, values);
          message.success("数据域已更新");
          setEditing(null);
          await refresh();
          return true;
        } catch (error) {
          message.error((error as Error).message);
          return false;
        }
      }}
    >
      <ProFormText name="domain_name" label="数据域名称" rules={[{ required: true }]} />
      <ProFormText name="factory_code" label="厂家编码（可选）" />
      <ProFormSwitch name="active" label="启用" />
    </ModalForm>

    <ModalForm<GrantFormValues>
      key={granting?.data_domain_id ?? "grant-empty"}
      title={`授权数据域：${granting?.domain_name ?? ""}`}
      open={Boolean(granting)}
      onOpenChange={(open) => { if (!open) setGranting(null); }}
      modalProps={{ destroyOnHidden: true }}
      onFinish={async (values) => {
        if (!granting) return false;
        try {
          await grantDataDomain(granting.data_domain_id, {
            user_id: values.user_id,
            expires_at_utc: values.expires_at_utc ? new Date(values.expires_at_utc).toISOString() : null,
            reason: values.reason,
          });
          message.success("数据域授权已生效");
          setGranting(null);
          await refresh();
          return true;
        } catch (error) {
          message.error((error as Error).message);
          return false;
        }
      }}
    >
      <ProFormSelect name="user_id" label="授权用户" showSearch options={grantOptions} rules={[{ required: true }]} />
      <ProFormText name="expires_at_utc" label="到期时间（可选，本机时间）" fieldProps={{ type: "datetime-local" }} />
      <ProFormText name="reason" label="授权原因" rules={[{ required: true, min: 2 }]} />
    </ModalForm>
  </div>;
}

export default DataDomainManagement;
