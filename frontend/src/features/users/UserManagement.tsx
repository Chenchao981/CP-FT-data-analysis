import { ModalForm, ProFormSelect, ProFormText, ProTable } from "@ant-design/pro-components";
import type { ProColumns } from "@ant-design/pro-components";
import { App, Button, Tag, Typography } from "antd";
import { useEffect, useState } from "react";
import { getRoles, getUsers, updateUser, UserRecord } from "../../api/auth";
export function UserManagement() {
  const { message } = App.useApp(); const [users, setUsers] = useState<UserRecord[]>([]); const [roles, setRoles] = useState<{ role_code: string; role_name: string }[]>([]); const [editing, setEditing] = useState<UserRecord | null>(null); const [loading, setLoading] = useState(true);
  const refresh = async () => { setLoading(true); try { const [u, r] = await Promise.all([getUsers(), getRoles()]); setUsers(u); setRoles(r); } catch (error) { message.error((error as Error).message); } finally { setLoading(false); } };
  useEffect(() => { void refresh(); }, []);
  const columns: ProColumns<UserRecord>[] = [
    { title: "用户", dataIndex: "display_name", render: (_, row) => <><strong>{row.display_name}</strong><br/><Typography.Text type="secondary">{row.login_name}</Typography.Text></> },
    { title: "部门", dataIndex: "department_code", renderText: (value) => value || "—" },
    { title: "角色", dataIndex: "roles", render: (_, row) => row.roles.length ? row.roles.map((role) => <Tag key={role} color="blue">{role}</Tag>) : <Tag>未分配</Tag> },
    { title: "状态", dataIndex: "status", valueEnum: { ACTIVE: { text: "正常", status: "Success" }, PENDING: { text: "待审批", status: "Processing" }, LOCKED: { text: "锁定", status: "Warning" }, DISABLED: { text: "停用", status: "Default" } } },
    { title: "最近登录", dataIndex: "last_login_at_utc", valueType: "dateTime", renderText: (value) => value || "从未登录" },
    { title: "操作", valueType: "option", render: (_, row) => <Button type="link" onClick={() => setEditing(row)}>权限设置</Button> },
  ];
  return <div className="workbench"><div className="page-heading"><Typography.Title level={2}>用户与功能权限</Typography.Title></div><ProTable<UserRecord> rowKey="user_id" search={false} loading={loading} dataSource={users} columns={columns} pagination={{ pageSize: 10 }} toolBarRender={() => [<Button key="refresh" onClick={() => void refresh()}>刷新</Button>]} />
    <ModalForm title={`权限设置：${editing?.display_name ?? ""}`} open={Boolean(editing)} onOpenChange={(open) => { if (!open) setEditing(null); }} initialValues={editing ? { status: editing.status, role_codes: editing.roles, department_code: editing.department_code } : {}} modalProps={{ destroyOnHidden: true }} onFinish={async (values) => { if (!editing) return false; try { await updateUser(editing.user_id, values as { status: string; role_codes: string[]; department_code?: string }); message.success("用户权限已更新"); setEditing(null); await refresh(); return true; } catch (error) { message.error((error as Error).message); return false; } }}>
      <ProFormSelect name="status" label="账户状态" options={[{ label: "正常", value: "ACTIVE" }, { label: "待审批", value: "PENDING" }, { label: "锁定", value: "LOCKED" }, { label: "停用", value: "DISABLED" }]} rules={[{ required: true }]} /><ProFormSelect name="role_codes" label="角色" mode="multiple" options={roles.map((role) => ({ label: `${role.role_name}（${role.role_code}）`, value: role.role_code }))} /><ProFormText name="department_code" label="部门编码" />
    </ModalForm></div>;
}
