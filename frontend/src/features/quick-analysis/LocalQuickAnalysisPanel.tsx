import {
  CheckCircleOutlined,
  DeleteOutlined,
  DesktopOutlined,
  FolderOpenOutlined,
  LinkOutlined,
  PlayCircleOutlined,
  SafetyCertificateOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Input,
  List,
  Row,
  Segmented,
  Space,
  Statistic,
  Tag,
  message,
} from "antd";
import { useEffect, useMemo, useState } from "react";

import {
  clearLocalAgentRunReference,
  deleteLocalRun,
  getLocalAgentHealth,
  getLocalRun,
  getLocalRunReceipt,
  getLocalRunResult,
  listLocalAgentTools,
  previewLocalSelection,
  runLocalSelection,
  saveLocalAgentRunReference,
  saveLocalAgentToken,
  selectLocalFolder,
  storedLocalAgentToken,
  storedLocalAgentRunReference,
  type LocalManifestPreview,
  type LocalRun,
  type LocalSelection,
  type LocalToolCapability,
} from "../../api/localAgent";
import { getLocalQuickCapability, registerLocalQuickResult } from "../../api/quickAnalysis";

const size = (value?: number | null) => {
  if (value == null) return "—";
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(2)} MB`;
  return `${(value / 1024 / 1024 / 1024).toFixed(2)} GB`;
};

const runStatusName: Record<string, string> = {
  QUEUED: "等待本机计算",
  RUNNING: "本机计算中",
  SUCCESS: "本机计算完成",
  FAILED: "本机计算失败",
};

const runStatusColor: Record<string, string> = {
  QUEUED: "gold",
  RUNNING: "processing",
  SUCCESS: "success",
  FAILED: "error",
};

export interface LocalQuickAnalysisPanelProps {
  onRegistered: () => void | Promise<void>;
}

export function LocalQuickAnalysisPanel({ onRegistered }: LocalQuickAnalysisPanelProps) {
  const [initialRunReference] = useState(() => storedLocalAgentRunReference());
  const [token, setToken] = useState(() => storedLocalAgentToken() ?? "");
  const [connectedToken, setConnectedToken] = useState<string | undefined>(
    () => storedLocalAgentToken() ?? undefined,
  );
  const [tools, setTools] = useState<LocalToolCapability[]>([]);
  const [stage, setStage] = useState<"CP" | "FT">("FT");
  const [toolCode, setToolCode] = useState<string>();
  const [selection, setSelection] = useState<LocalSelection>();
  const [preview, setPreview] = useState<LocalManifestPreview>();
  const [runId, setRunId] = useState<string | undefined>(initialRunReference?.run_id);
  const [registeredSessionId, setRegisteredSessionId] = useState<number | undefined>(
    initialRunReference?.registered_session_id ?? undefined,
  );
  const [messageApi, contextHolder] = message.useMessage();

  const health = useQuery({
    queryKey: ["local-agent", "health"],
    queryFn: getLocalAgentHealth,
    retry: false,
    refetchInterval: 10_000,
  });
  const serverCapability = useQuery({
    queryKey: ["quick-analysis", "local-capability"],
    queryFn: getLocalQuickCapability,
    retry: false,
  });
  const connect = useMutation({
    mutationFn: async () => {
      const normalized = token.trim();
      if (!normalized) throw new Error("请输入 Agent 启动窗口显示的配对令牌");
      const result = await listLocalAgentTools(normalized);
      saveLocalAgentToken(normalized);
      return { normalized, result };
    },
    onSuccess: ({ normalized, result }) => {
      setConnectedToken(normalized);
      setTools(result);
      setSelection(undefined);
      setPreview(undefined);
      const first = result.find((item) => item.test_stage === stage && item.enabled)
        ?? result.find((item) => item.test_stage === stage);
      setToolCode(first?.tool_code);
      messageApi.success("已连接本机 Agent");
    },
    onError: (error) => messageApi.error(error.message),
  });
  const availableTools = useMemo(
    () => tools.filter((item) => item.test_stage === stage),
    [stage, tools],
  );
  const selectedTool = tools.find((item) => item.tool_code === toolCode);
  useEffect(() => {
    if (selectedTool?.test_stage === stage) return;
    const first = availableTools.find((item) => item.enabled) ?? availableTools[0];
    setToolCode(first?.tool_code);
    setSelection(undefined);
    setPreview(undefined);
  }, [availableTools, selectedTool?.test_stage, stage]);

  const selectMutation = useMutation({
    mutationFn: async () => {
      if (runId) throw new Error("请先完成或清理当前本机任务");
      if (!selectedTool?.enabled) throw new Error(selectedTool?.disabled_reason ?? "当前能力不可用");
      const selected = await selectLocalFolder();
      const manifest = await previewLocalSelection(selected.selection_id, selectedTool.tool_code);
      return { selected, manifest };
    },
    onSuccess: ({ selected, manifest }) => {
      setSelection(selected);
      setPreview(manifest);
      setRegisteredSessionId(undefined);
    },
    onError: (error) => messageApi.error(error.message),
  });
  const runMutation = useMutation({
    mutationFn: () => runLocalSelection(selection!.selection_id, selectedTool!.tool_code, preview!.sha256),
    onSuccess: (run) => {
      saveLocalAgentRunReference(run.run_id);
      setRunId(run.run_id);
      setRegisteredSessionId(undefined);
      messageApi.success("已在本机启动计算，源文件不会上传到 TMS");
    },
    onError: (error) => messageApi.error(error.message),
  });
  const run = useQuery<LocalRun>({
    queryKey: ["local-agent", "run", runId],
    queryFn: () => getLocalRun(runId!),
    enabled: Boolean(runId && connectedToken),
    retry: false,
    refetchInterval: (query) => ["QUEUED", "RUNNING"].includes(query.state.data?.status ?? "") ? 2_000 : false,
  });
  const registerMutation = useMutation({
    mutationFn: async () => {
      const receipt = await getLocalRunReceipt(runId!);
      const result = await getLocalRunResult(runId!);
      if (result.size !== receipt.result.size_bytes) {
        throw new Error("本机结果大小与一致性回执不一致，已停止登记");
      }
      return registerLocalQuickResult(receipt, result);
    },
    onSuccess: async (session) => {
      setRegisteredSessionId(session.analysis_session_id);
      saveLocalAgentRunReference(runId!, session.analysis_session_id);
      messageApi.success(`结果已登记为个人快速分析会话 ${session.analysis_session_id}`);
      try {
        await deleteLocalRun(runId!);
        clearLocalAgentRunReference(runId!);
        setRunId(undefined);
      } catch (error) {
        messageApi.warning(`结果已登记，但本机临时目录清理失败：${(error as Error).message}`);
      }
      await onRegistered();
    },
    onError: (error) => messageApi.error(error.message),
  });
  const cleanupMutation = useMutation({
    mutationFn: async () => {
      const activeRunId = runId!;
      await deleteLocalRun(activeRunId);
      return activeRunId;
    },
    onSuccess: (cleanedRunId) => {
      clearLocalAgentRunReference(cleanedRunId);
      setRunId(undefined);
      messageApi.success("本机任务记录和临时目录已清理");
    },
    onError: (error) => messageApi.error(error.message),
  });

  const forgetUnavailableRun = () => {
    if (runId) clearLocalAgentRunReference(runId);
    setRunId(undefined);
    setRegisteredSessionId(undefined);
  };

  const releaseMatches = Boolean(
    selectedTool?.enabled
    && serverCapability.data
    && selectedTool.tool_code === serverCapability.data.tool_code
    && selectedTool.package_sha256?.toLowerCase() === serverCapability.data.release.sha256.toLowerCase()
    && selectedTool.timeout_seconds === serverCapability.data.release.timeout_seconds
    && selectedTool.max_output_bytes === serverCapability.data.release.max_output_bytes
  );
  const canRun = Boolean(selection && preview && selectedTool?.enabled && releaseMatches);

  return <div className="local-quick-panel">
    {contextHolder}
    <Card
      title={<Space><LinkOutlined />本机 Agent</Space>}
      extra={<Tag color={health.isSuccess ? "success" : "default"}>{health.isSuccess ? "端口在线" : "未检测到"}</Tag>}
      className="quick-source-card"
    >
      <Row gutter={[12, 12]} align="middle">
        <Col flex="auto">
          <Input.Password
            aria-label="本机 Agent 配对令牌"
            placeholder="粘贴 Agent 启动窗口中的一次配对令牌"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            onPressEnter={() => connect.mutate()}
          />
        </Col>
        <Col><Button type="primary" loading={connect.isPending} onClick={() => connect.mutate()}>连接</Button></Col>
      </Row>
    </Card>

    <Card
      title={<Space><DesktopOutlined />选择路线和本机目录</Space>}
      className="quick-source-card"
      extra={<Button
        type="primary"
        icon={<FolderOpenOutlined />}
        disabled={!connectedToken || !selectedTool?.enabled || Boolean(runId)}
        loading={selectMutation.isPending}
        onClick={() => selectMutation.mutate()}
      >选择本机目录</Button>}
    >
      <Space direction="vertical" size={14} style={{ width: "100%" }}>
        <Segmented
          value={stage}
          options={[{ label: "FT 工具", value: "FT" }, { label: "CP 工具", value: "CP" }]}
          onChange={(value) => setStage(value as "CP" | "FT")}
        />
        {connectedToken && <List
          bordered
          dataSource={availableTools}
          locale={{ emptyText: `${stage} 尚未注册本机分析能力` }}
          renderItem={(item) => <List.Item
            actions={[<Button key="choose" type={toolCode === item.tool_code ? "primary" : "default"} disabled={!item.enabled} onClick={() => setToolCode(item.tool_code)}>{toolCode === item.tool_code ? "已选择" : "选择"}</Button>]}
          >
            <List.Item.Meta
              avatar={item.enabled ? <CheckCircleOutlined style={{ color: "#52c41a" }} /> : <SafetyCertificateOutlined style={{ color: "#faad14" }} />}
              title={<Space>{item.display_name}<Tag>{item.factory_code}</Tag></Space>}
              description={item.enabled ? undefined : item.disabled_reason}
            />
          </List.Item>}
        />}
        {selectedTool?.enabled && !releaseMatches && <Alert
          type="error"
          showIcon
          message="Agent 与 TMS 登记的工具合同不一致"
        />}
        {preview && <>
          <Descriptions bordered size="small" column={{ xs: 1, sm: 2 }}>
            <Descriptions.Item label="目录">{preview.source_label}</Descriptions.Item>
            <Descriptions.Item label="Manifest">{preview.sha256.slice(0, 16)}…</Descriptions.Item>
            <Descriptions.Item label="源文件">{preview.file_count.toLocaleString("zh-CN")}</Descriptions.Item>
            <Descriptions.Item label="源数据量">{size(preview.total_bytes)}</Descriptions.Item>
            <Descriptions.Item label="允许类型">{preview.allowed_suffixes.join("、")}</Descriptions.Item>
          </Descriptions>
          <Button
            type="primary"
            size="large"
            icon={<PlayCircleOutlined />}
            disabled={!canRun || Boolean(runId)}
            loading={runMutation.isPending}
            onClick={() => runMutation.mutate()}
          >确认 Manifest 并在本机计算</Button>
        </>}
      </Space>
    </Card>

    {runId && <Card title="本机运行与结果登记" className="quick-source-card">
      {run.data && <>
        <Row gutter={[12, 12]}>
          <Col xs={12} md={6}><Statistic title="状态" value={runStatusName[run.data.status] ?? run.data.status} /></Col>
          <Col xs={12} md={6}><Statistic title="参数数" value={run.data.parameter_count ?? "—"} /></Col>
          <Col xs={12} md={6}><Statistic title="解析行" value={run.data.record_count ?? "—"} /></Col>
          <Col xs={12} md={6}><Statistic title="本机耗时" value={run.data.elapsed_seconds == null ? "—" : `${run.data.elapsed_seconds.toFixed(3)} 秒`} /></Col>
        </Row>
        <Space wrap style={{ marginTop: 16 }}>
          <Tag color={runStatusColor[run.data.status]}>{runStatusName[run.data.status] ?? run.data.status}</Tag>
          {run.data.status === "SUCCESS" && <Button
            type="primary"
            icon={<UploadOutlined />}
            disabled={registeredSessionId !== undefined}
            loading={registerMutation.isPending}
            onClick={() => registerMutation.mutate()}
          >仅上传并登记结果</Button>}
          {registeredSessionId && <Tag color="success">已登记：会话 {registeredSessionId}</Tag>}
          {run.data.status === "FAILED" && <Button
            icon={<DeleteOutlined />}
            loading={cleanupMutation.isPending}
            onClick={() => cleanupMutation.mutate()}
          >清理失败任务</Button>}
          {registeredSessionId && <Button
            icon={<DeleteOutlined />}
            loading={cleanupMutation.isPending}
            onClick={() => cleanupMutation.mutate()}
          >重试清理本机临时目录</Button>}
        </Space>
        {run.data.status === "FAILED" && <Alert style={{ marginTop: 12 }} type="error" showIcon message={run.data.error_message ?? "本机工具运行失败"} description={run.data.error_code} />}
      </>}
      {run.isError && <Alert
        type="error"
        showIcon
        message="本机运行状态读取失败"
        description={run.error.message}
        action={<Button size="small" danger onClick={forgetUnavailableRun}>清除失效任务引用</Button>}
      />}
    </Card>}
    {!runId && registeredSessionId && <Alert
      type="success"
      showIcon
      message={`结果已登记为个人快速分析会话 ${registeredSessionId}`}
    />}
  </div>;
}
