import { LockOutlined, SafetyCertificateOutlined, UserOutlined } from "@ant-design/icons";
import { LoginFormPage, ProFormText } from "@ant-design/pro-components";
import { App, Button, Modal, Typography } from "antd";
import { useState } from "react";
import { register } from "../../api/auth";
import { useAuth } from "./AuthContext";
type LoginValues = { login_name: string; password: string };
type RegisterValues = LoginValues & { display_name: string; confirm_password: string; email?: string; department_code?: string };
export function LoginPage() {
  const { message } = App.useApp(); const { login } = useAuth(); const [registerOpen, setRegisterOpen] = useState(false); const [submitting, setSubmitting] = useState(false);
  return <div className="login-shell"><LoginFormPage<LoginValues> backgroundImageUrl="" logo={<div className="login-logo">T</div>} title="TMS 制造测试数据中心" subTitle="CP / FT 数据清洗、治理与分析平台" onFinish={async (values) => { setSubmitting(true); try { await login(values.login_name, values.password); } catch (error) { message.error((error as Error).message); } finally { setSubmitting(false); } return true; }} submitter={{ submitButtonProps: { loading: submitting, block: true, size: "large" }, searchConfig: { submitText: "登录" } }}>
    <ProFormText name="login_name" fieldProps={{ size: "large", prefix: <UserOutlined /> }} placeholder="登录名" rules={[{ required: true, message: "请输入登录名" }]} />
    <ProFormText.Password name="password" fieldProps={{ size: "large", prefix: <LockOutlined /> }} placeholder="密码" rules={[{ required: true, message: "请输入密码" }]} />
    <div className="login-actions"><Typography.Text type="secondary"><SafetyCertificateOutlined /> 账户权限由管理员审批</Typography.Text><Button type="link" onClick={() => setRegisterOpen(true)}>申请注册</Button></div>
  </LoginFormPage><Modal title="申请注册" open={registerOpen} onCancel={() => setRegisterOpen(false)} footer={null} destroyOnHidden>
    <LoginFormPage<RegisterValues> className="register-form" logo={false} title={false} subTitle={false} onFinish={async (values) => { if (values.password !== values.confirm_password) { message.error("两次输入的密码不一致"); return false; } try { await register(values); message.success("注册已提交，请等待管理员启用账户"); setRegisterOpen(false); return true; } catch (error) { message.error((error as Error).message); return false; } }} submitter={{ searchConfig: { submitText: "提交申请" }, resetButtonProps: { style: { display: "none" } } }}>
      <ProFormText name="login_name" label="登录名" rules={[{ required: true }, { min: 3 }]} /><ProFormText name="display_name" label="姓名" rules={[{ required: true }]} /><ProFormText name="email" label="邮箱（可选）" /><ProFormText name="department_code" label="部门编码（可选）" /><ProFormText.Password name="password" label="密码" rules={[{ required: true }, { min: 8 }]} /><ProFormText.Password name="confirm_password" label="确认密码" rules={[{ required: true }]} />
    </LoginFormPage></Modal></div>;
}
