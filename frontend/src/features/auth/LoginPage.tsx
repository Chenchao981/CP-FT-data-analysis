import {
  ArrowRightOutlined,
  DatabaseOutlined,
  DeploymentUnitOutlined,
  LineChartOutlined,
  LockOutlined,
  SafetyCertificateOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { App, Button, Form, Input, Modal, Space, Tag, Typography } from "antd";
import { useState } from "react";

import { register } from "../../api/auth";
import { useAuth } from "./AuthContext";

type LoginValues = { login_name: string; password: string };
type RegisterValues = LoginValues & { display_name: string; confirm_password: string; email?: string; department_code?: string };

const platformCapabilities = [
  { icon: <DeploymentUnitOutlined />, title: "版本化 Cleaner", text: "CP / FT 厂家规则隔离，Release 与 SHA 全程可追溯" },
  { icon: <DatabaseOutlined />, title: "Canonical 数据链", text: "从 Source、Run、Unit 到 Measurement 的唯一正式事实链" },
  { icon: <LineChartOutlined />, title: "治理型分析", text: "良率、Bin、参数、空间与质量门禁统一呈现" },
] as const;

export function LoginPage() {
  const { message } = App.useApp();
  const { login } = useAuth();
  const [registerOpen, setRegisterOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const submitLogin = async (values: LoginValues) => {
    setSubmitting(true);
    try {
      await login(values.login_name, values.password);
    } catch (error) {
      message.error((error as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  const submitRegistration = async (values: RegisterValues) => {
    if (values.password !== values.confirm_password) {
      message.error("两次输入的密码不一致");
      return;
    }
    try {
      await register(values);
      message.success("注册已提交，请等待管理员启用账户");
      setRegisterOpen(false);
    } catch (error) {
      message.error((error as Error).message);
    }
  };

  return <main className="login-shell">
    <div className="login-grid-overlay" aria-hidden="true" />
    <section className="login-story">
      <div className="login-brand"><div className="login-logo">T</div><div><b>TMS</b><span>TEST DATA INTELLIGENCE</span></div></div>
      <div className="login-story-copy">
        <Tag className="login-eyebrow">POWER SEMICONDUCTOR DATA HUB</Tag>
        <Typography.Title>让每一颗芯片的测试数据<br/><span>可追溯 · 可解释 · 可行动</span></Typography.Title>
        <p className="login-story-description">连接晶圆 CP、封装 FT、质量治理与经营视图，为制造决策提供同一份可信事实。</p>
      </div>
      <div className="login-capabilities">
        {platformCapabilities.map((item) => <div key={item.title}><i>{item.icon}</i><span><b>{item.title}</b><small>{item.text}</small></span></div>)}
      </div>
      <div className="login-wafer-visual" aria-hidden="true"><div className="login-wafer-ring ring-a"/><div className="login-wafer-ring ring-b"/><div className="login-wafer-die die-a"/><div className="login-wafer-die die-b"/><div className="login-wafer-die die-c"/></div>
      <footer>WUXI NCE POWER · DIGITAL MANUFACTURING</footer>
    </section>

    <section className="login-panel-wrap">
      <div className="login-panel">
        <Space size={8}><Tag color="cyan">SECURE ACCESS</Tag><span className="login-live-dot">服务可用</span></Space>
        <Typography.Title level={2}>欢迎进入 TMS</Typography.Title>
        <p className="login-panel-description">使用已启用的企业账号继续</p>
        <Form<LoginValues> layout="vertical" requiredMark={false} onFinish={submitLogin} size="large" className="login-form">
          <Form.Item name="login_name" label="登录名" rules={[{ required: true, message: "请输入登录名" }]}>
            <Input prefix={<UserOutlined />} placeholder="请输入登录名" autoComplete="username" />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true, message: "请输入密码" }]}>
            <Input.Password prefix={<LockOutlined />} placeholder="请输入密码" autoComplete="current-password" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block size="large" loading={submitting} className="login-submit">登录系统 <ArrowRightOutlined /></Button>
        </Form>
        <div className="login-actions"><Typography.Text type="secondary"><SafetyCertificateOutlined /> 权限由管理员审批</Typography.Text><Button type="link" onClick={() => setRegisterOpen(true)}>申请注册</Button></div>
        <div className="login-security-note"><SafetyCertificateOutlined /><span><b>安全提示</b><small>系统记录登录与关键操作审计，请勿共享账号。</small></span></div>
      </div>
      <div className="login-version">TMS Route A · Candidate Environment</div>
    </section>

    <Modal title="申请注册" open={registerOpen} onCancel={() => setRegisterOpen(false)} footer={null} destroyOnHidden>
      <Form<RegisterValues> layout="vertical" requiredMark={false} onFinish={submitRegistration} className="register-form">
        <Form.Item name="login_name" label="登录名" rules={[{ required: true }, { min: 3 }]}><Input /></Form.Item>
        <Form.Item name="display_name" label="姓名" rules={[{ required: true }]}><Input /></Form.Item>
        <Form.Item name="email" label="邮箱（可选）"><Input /></Form.Item>
        <Form.Item name="department_code" label="部门编码（可选）"><Input /></Form.Item>
        <Form.Item name="password" label="密码" rules={[{ required: true }, { min: 8 }]}><Input.Password /></Form.Item>
        <Form.Item name="confirm_password" label="确认密码" rules={[{ required: true }]}><Input.Password /></Form.Item>
        <Button type="primary" htmlType="submit" block>提交申请</Button>
      </Form>
    </Modal>
  </main>;
}
