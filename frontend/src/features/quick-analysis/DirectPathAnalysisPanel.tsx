import {
  ArrowUpOutlined,
  BarChartOutlined,
  CheckCircleFilled,
  DatabaseOutlined,
  ExperimentOutlined,
  FileExcelOutlined,
  FileOutlined,
  FileZipOutlined,
  FolderOpenOutlined,
  PlayCircleOutlined,
  SearchOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import { useMutation } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Card,
  Col,
  Input,
  List,
  Modal,
  Row,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMemo, useState } from "react";

import {
  browseDirectPath,
  createDirectPathPat,
  previewDirectPath,
  type DirectPathBrowseItem,
  type DirectPathPreview,
  type DirectPathToolCode,
} from "../../api/quickAnalysis";

type WorkbenchStage = "CP" | "FT";
type OperationCode = "CLEAN" | "CHART" | "PAT" | "SYL_SBL" | "DIE_COUNT";
type BrowserPurpose = "INPUT" | "OUTPUT";

interface OperationConfig {
  code: OperationCode;
  name: string;
  available: boolean;
  icon: React.ReactNode;
}

interface FactoryConfig {
  code: string;
  name: string;
  englishName: string;
  formats: string;
  toolCode: DirectPathToolCode;
  operations: OperationConfig[];
}

const operation = (
  code: OperationCode,
  name: string,
  icon: React.ReactNode,
  available = false,
): OperationConfig => ({ code, name, icon, available });

const patOperation = operation(
  "PAT",
  "PAT 参数分析",
  <ExperimentOutlined />,
  true,
);

const FACTORIES: Record<WorkbenchStage, FactoryConfig[]> = {
  CP: [
    {
      code: "HUAHONG",
      name: "华虹",
      englishName: "HuaHong",
      formats: "DCP/TXT · ZIP/7z",
      toolCode: "HUAHONG_CP_QUICK_PAT_EXISTING",
      operations: [
        operation("CLEAN", "数据清洗", <DatabaseOutlined />),
        operation("CHART", "图表分析", <BarChartOutlined />),
        patOperation,
      ],
    },
    {
      code: "JETECH",
      name: "积塔",
      englishName: "Jetech",
      formats: "XLS/XLSX · ZIP",
      toolCode: "JETECH_CP_QUICK_PAT_EXISTING",
      operations: [
        operation("CLEAN", "数据清洗", <DatabaseOutlined />),
        operation("CHART", "图表分析", <BarChartOutlined />),
        patOperation,
      ],
    },
    {
      code: "LION",
      name: "立昂微",
      englishName: "Lion",
      formats: "XLS/XLSX · ZIP",
      toolCode: "LION_CP_QUICK_PAT_EXISTING",
      operations: [
        operation("CLEAN", "数据清洗", <DatabaseOutlined />),
        operation("CHART", "图表分析", <BarChartOutlined />),
        patOperation,
        operation("DIE_COUNT", "管芯数汇总", <FileExcelOutlined />),
      ],
    },
    {
      code: "GUOYU",
      name: "国宇 FRD",
      englishName: "Guoyu",
      formats: "XLS/XLSX · ZIP",
      toolCode: "GUOYU_CP_QUICK_PAT_EXISTING",
      operations: [
        operation("CLEAN", "数据清洗", <DatabaseOutlined />),
        operation("CHART", "图表分析", <BarChartOutlined />),
        patOperation,
      ],
    },
  ],
  FT: [
    {
      code: "RIYUEXIN",
      name: "日月新",
      englishName: "Riyuexin",
      formats: "DC · DVDS · RG · XLSX · ZIP/7z",
      toolCode: "RIYUEXIN_FT_QUICK_PAT_EXISTING",
      operations: [
        operation("CLEAN", "FT 数据清洗", <DatabaseOutlined />),
        operation("CHART", "FT 散点图", <BarChartOutlined />),
        patOperation,
        operation("SYL_SBL", "SBL & SYL", <FileExcelOutlined />),
      ],
    },
    {
      code: "JIEQUN",
      name: "杰群",
      englishName: "Jiequn",
      formats: "DC-AI · DVDS · RG · CSV · ZIP/7z",
      toolCode: "JIEQUN_FT_QUICK_PAT_EXISTING",
      operations: [
        operation("CLEAN", "FT 数据清洗", <DatabaseOutlined />),
        operation("CHART", "FT 散点图", <BarChartOutlined />),
        patOperation,
        operation("SYL_SBL", "SBL & SYL", <FileExcelOutlined />),
      ],
    },
    {
      code: "RIYUEGUANG",
      name: "日月光",
      englishName: "ASE",
      formats: "DC · DVDS · RG · XLSX · ZIP/7z",
      toolCode: "RIYUEGUANG_FT_QUICK_PAT_EXISTING",
      operations: [
        operation("CLEAN", "FT 数据清洗", <DatabaseOutlined />),
        patOperation,
      ],
    },
    {
      code: "DIANJI",
      name: "电基",
      englishName: "Dianji",
      formats: "XLS/XLSX/CSV · ZIP/7z",
      toolCode: "DIANJI_FT_QUICK_PAT_EXISTING",
      operations: [
        operation("CLEAN", "FT-ALL 清洗", <DatabaseOutlined />),
        operation("CHART", "FT 散点图", <BarChartOutlined />),
        patOperation,
        operation("SYL_SBL", "SBL & SYL", <FileExcelOutlined />),
      ],
    },
    {
      code: "JIJIA",
      name: "集佳",
      englishName: "Jijia",
      formats: "FT-ALL · CSV · ZIP/7z",
      toolCode: "JIJIA_FT_QUICK_PAT_EXISTING",
      operations: [
        operation("CLEAN", "FT-ALL 清洗", <DatabaseOutlined />),
        patOperation,
      ],
    },
  ],
};

const displaySize = (value: number) => {
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(2)} MB`;
  return `${(value / 1024 / 1024 / 1024).toFixed(2)} GB`;
};

export function DirectPathAnalysisPanel({ onCreated }: { onCreated: () => void }) {
  const [stage, setStage] = useState<WorkbenchStage>("FT");
  const [factoryCode, setFactoryCode] = useState("RIYUEXIN");
  const [selectedOperation, setSelectedOperation] = useState<OperationCode>("PAT");
  const [paths, setPaths] = useState<Record<string, { input: string; output: string }>>({});
  const [preview, setPreview] = useState<DirectPathPreview>();
  const [browserOpen, setBrowserOpen] = useState(false);
  const [browserPurpose, setBrowserPurpose] = useState<BrowserPurpose>("INPUT");
  const [browserPath, setBrowserPath] = useState("");
  const [messageApi, contextHolder] = message.useMessage();

  const factories = FACTORIES[stage];
  const factory = factories.find((item) => item.code === factoryCode) ?? factories[0];
  const pathKey = `${stage}:${factory.code}`;
  const currentPaths = paths[pathKey] ?? { input: "", output: "" };
  const selected = factory.operations.find((item) => item.code === selectedOperation) ?? patOperation;

  const updatePath = (field: "input" | "output", value: string) => {
    setPaths((current) => ({
      ...current,
      [pathKey]: { ...(current[pathKey] ?? { input: "", output: "" }), [field]: value },
    }));
    if (field === "input") setPreview(undefined);
  };

  const previewMutation = useMutation({
    mutationFn: () => previewDirectPath(currentPaths.input, factory.toolCode),
    onSuccess: (value) => setPreview(value),
    onError: (error) => { setPreview(undefined); messageApi.error(error.message); },
  });
  const browseMutation = useMutation({
    mutationFn: (targetPath: string) => browseDirectPath(targetPath, factory.toolCode),
    onSuccess: (value) => {
      setBrowserPath(value.path ?? "");
      setBrowserOpen(true);
    },
    onError: (error) => messageApi.error(error.message),
  });
  const runMutation = useMutation({
    mutationFn: () => createDirectPathPat(preview!, currentPaths.output),
    onSuccess: (session) => {
      messageApi.success(`个人 PAT ${session.analysis_session_id} 已进入后台队列，完成后结果保存到服务器历史记录`);
      onCreated();
    },
    onError: (error) => messageApi.error(error.message),
  });

  const chooseFactory = (nextFactory: FactoryConfig) => {
    setFactoryCode(nextFactory.code);
    setSelectedOperation("PAT");
    setPreview(undefined);
    setBrowserPath("");
  };
  const chooseStage = (nextStage: WorkbenchStage) => {
    const firstFactory = FACTORIES[nextStage][0];
    setStage(nextStage);
    setFactoryCode(firstFactory.code);
    setSelectedOperation("PAT");
    setPreview(undefined);
    setBrowserPath("");
  };
  const openBrowser = (purpose: BrowserPurpose) => {
    setBrowserPurpose(purpose);
    const initial = purpose === "INPUT" ? currentPaths.input : currentPaths.output;
    browseMutation.mutate(initial.trim());
  };
  const chooseBrowserPath = (selectedPath: string) => {
    updatePath(browserPurpose === "INPUT" ? "input" : "output", selectedPath);
    setBrowserOpen(false);
  };

  const browseColumns: ColumnsType<DirectPathBrowseItem> = useMemo(() => [
    {
      title: "名称",
      dataIndex: "name",
      ellipsis: true,
      render: (name, row) => {
        const isDirectory = row.kind === "DIRECTORY";
        const canChooseFile = browserPurpose === "INPUT" && row.selectable;
        return <Button
          type="link"
          disabled={!isDirectory && !canChooseFile}
          title={row.selection_hint ?? undefined}
          icon={isDirectory ? <FolderOpenOutlined /> : row.is_archive ? <FileZipOutlined /> : <FileOutlined />}
          onClick={() => isDirectory ? browseMutation.mutate(row.path) : chooseBrowserPath(row.path)}
        >{name}</Button>;
      },
    },
    {
      title: "类型",
      dataIndex: "kind",
      width: 100,
      render: (_, row) => row.kind === "DIRECTORY"
        ? <Tag color="blue">文件夹</Tag>
        : row.is_archive ? <Tag color="purple">压缩包</Tag> : <Tag>源文件</Tag>,
    },
    { title: "大小", dataIndex: "size_bytes", width: 120, render: (value: number | null) => value == null ? "—" : displaySize(value) },
    {
      title: "操作",
      key: "action",
      width: 120,
      render: (_, row) => row.kind === "DIRECTORY"
        ? <Button size="small" onClick={() => browseMutation.mutate(row.path)}>打开</Button>
        : browserPurpose === "INPUT" && row.selectable
          ? <Button size="small" onClick={() => chooseBrowserPath(row.path)}>选择</Button>
          : "—",
    },
  ], [browserPurpose, browseMutation]);

  const canRunPat = selected.code === "PAT" && Boolean(preview);

  return <div className="personal-tool-workbench">
    {contextHolder}
    <Card className="tool-stage-card">
      <div className="tool-stage-heading">
        <div>
          <Typography.Title level={4}>1. 选择 CP 或 FT 工具</Typography.Title>
        </div>
        <div className="stage-switch" role="group" aria-label="工具类型">
          {(["CP", "FT"] as const).map((item) => <button
            key={item}
            type="button"
            className={stage === item ? "active" : ""}
            aria-pressed={stage === item}
            onClick={() => chooseStage(item)}
          >
            {item === "CP" ? <DatabaseOutlined /> : <ThunderboltOutlined />}
            <span><strong>{item} 工具</strong><small>{item === "CP" ? "晶圆测试" : "成品测试"}</small></span>
            {stage === item && <CheckCircleFilled />}
          </button>)}
        </div>
      </div>
    </Card>

    <Card className="tool-step-card" title="2. 选择厂家">
      <div className="factory-grid">
        {factories.map((item) => <button
          key={item.code}
          type="button"
          className={factory.code === item.code ? "factory-tile active" : "factory-tile"}
          aria-pressed={factory.code === item.code}
          onClick={() => chooseFactory(item)}
        >
          <span className="factory-mark">{item.name.slice(0, 1)}</span>
          <span><strong>{item.name}</strong><small>{item.englishName}</small></span>
          {factory.code === item.code && <CheckCircleFilled />}
        </button>)}
      </div>
      <div className="factory-format-line">
        <Tag color={stage === "CP" ? "blue" : "cyan"}>{stage}</Tag>
        <Typography.Text strong>{factory.name}</Typography.Text>
        <Typography.Text type="secondary">{factory.formats}</Typography.Text>
      </div>
    </Card>

    <Card className="tool-step-card" title="3. 选择输入路径">
      <div className="path-form-grid">
        <label htmlFor="quick-input-path">输入路径</label>
        <Space.Compact className="path-input-group">
          <Input
            id="quick-input-path"
            aria-label="输入路径"
            value={currentPaths.input}
            placeholder={String.raw`例如 F:\data\CP和FT源数据\...\产品目录或压缩包`}
            onChange={(event) => updatePath("input", event.target.value)}
            onPressEnter={() => selected.code === "PAT" && currentPaths.input.trim() && previewMutation.mutate()}
          />
          <Button icon={<FolderOpenOutlined />} loading={browseMutation.isPending && browserPurpose === "INPUT"} onClick={() => openBrowser("INPUT")}>预览选择</Button>
          <Button icon={<SearchOutlined />} disabled={selected.code !== "PAT" || !currentPaths.input.trim()} loading={previewMutation.isPending} onClick={() => previewMutation.mutate()}>解析范围</Button>
        </Space.Compact>

        <label htmlFor="quick-output-path">额外导出（可选）</label>
        <Space.Compact className="path-input-group">
          <Input
            id="quick-output-path"
            aria-label="输出路径"
            value={currentPaths.output}
            placeholder="不填也会保存到服务器历史；需要本地副本时再选"
            onChange={(event) => updatePath("output", event.target.value)}
          />
          <Button icon={<FolderOpenOutlined />} loading={browseMutation.isPending && browserPurpose === "OUTPUT"} onClick={() => openBrowser("OUTPUT")}>预览选择</Button>
        </Space.Compact>
      </div>
    </Card>

    <Card className="tool-step-card" title="4. 选择需要执行的功能">
      <div className="operation-grid">
        {factory.operations.map((item) => <button
          key={item.code}
          type="button"
          className={`${selected.code === item.code ? "operation-tile active" : "operation-tile"}${item.available ? "" : " pending"}`}
          aria-pressed={selected.code === item.code}
          disabled={!item.available}
          onClick={() => { setSelectedOperation(item.code); setPreview(undefined); }}
        >
          <span className="operation-icon">{item.icon}</span>
          <span><strong>{item.name}</strong></span>
          <Tag color={item.available ? "success" : "default"}>{item.available ? "可运行" : "待接入"}</Tag>
        </button>)}
      </div>

      {selected.code === "PAT" && preview && <Card
        size="small"
        type="inner"
        className="source-preview-card"
        title={`已确认解析范围：${preview.source_label}`}
        extra={<Button
          type="primary"
          size="large"
          icon={<PlayCircleOutlined />}
          disabled={!canRunPat}
          loading={runMutation.isPending}
          onClick={() => runMutation.mutate()}
        >开始后台 PAT</Button>}
      >
        <Row gutter={[16, 16]}>
          <Col xs={24} md={8}><Statistic title="源文件" value={preview.file_count} /></Col>
          <Col xs={24} md={8}><Statistic title="源数据大小" value={displaySize(preview.total_bytes)} /></Col>
          <Col xs={24} md={8}><Statistic title="执行工具" value={`${stage} · ${factory.name}`} /></Col>
        </Row>
        <Space wrap className="source-preview-meta">
          <Tag color={preview.input_kind === "DIRECTORY" ? "blue" : "purple"}>{preview.input_kind === "DIRECTORY" ? "文件夹" : "单个文件"}</Tag>
          {preview.archive_count > 0 && <Tag color="purple">压缩包 {preview.archive_count} 个</Tag>}
          <Typography.Text><strong>输入：</strong><Typography.Text code copyable>{preview.path}</Typography.Text></Typography.Text>
          <Typography.Text><strong>服务器结果：</strong>自动保存到个人历史</Typography.Text>
          <Typography.Text><strong>额外导出：</strong><Typography.Text code copyable>{currentPaths.output || "不导出本地副本"}</Typography.Text></Typography.Text>
        </Space>
        <List
          size="small"
          header={<Typography.Text strong>将解析的文件{preview.sample_truncated ? "（前 100 个）" : ""}</Typography.Text>}
          dataSource={preview.sample_files}
          renderItem={(item) => <List.Item><Typography.Text code>{item}</Typography.Text></List.Item>}
          className="source-file-list"
        />
      </Card>}

    </Card>

    <Modal
      title={browserPurpose === "INPUT" ? `选择 ${factory.name} 输入数据` : "选择结果输出文件夹"}
      open={browserOpen}
      width={900}
      onCancel={() => setBrowserOpen(false)}
      footer={<Space>
        <Button onClick={() => setBrowserOpen(false)}>取消</Button>
        <Button type="primary" disabled={!browseMutation.data?.path} onClick={() => browseMutation.data?.path && chooseBrowserPath(browseMutation.data.path)}>
          {browserPurpose === "INPUT" ? "使用当前文件夹" : "选择当前文件夹"}
        </Button>
      </Space>}
    >
      {browserPurpose === "INPUT" && <Typography.Text type="secondary">
        支持格式：{browseMutation.data?.allowed_suffixes.join("、") || "读取中"}
      </Typography.Text>}
      <Space.Compact style={{ width: "100%", marginBottom: 12 }}>
        <Button icon={<ArrowUpOutlined />} disabled={!browseMutation.data?.parent_path} onClick={() => browseMutation.data?.parent_path && browseMutation.mutate(browseMutation.data.parent_path)}>上一级</Button>
        <Input aria-label="路径浏览地址" value={browserPath} placeholder="输入本地盘、映射盘或 UNC 路径" onChange={(event) => setBrowserPath(event.target.value)} onPressEnter={() => browseMutation.mutate(browserPath)} />
        <Button icon={<SearchOutlined />} loading={browseMutation.isPending} onClick={() => browseMutation.mutate(browserPath)}>转到</Button>
      </Space.Compact>
      {browseMutation.data?.truncated && <Alert type="warning" showIcon message="当前目录项目较多，仅显示前 2000 项，请进入更具体的文件夹。" style={{ marginBottom: 12 }} />}
      <Table
        rowKey="path"
        size="small"
        loading={browseMutation.isPending}
        columns={browseColumns}
        dataSource={browseMutation.data?.items ?? []}
        pagination={false}
        scroll={{ y: 420 }}
        onRow={(row) => ({
          onDoubleClick: () => row.kind === "DIRECTORY"
            ? browseMutation.mutate(row.path)
            : browserPurpose === "INPUT" && row.selectable && chooseBrowserPath(row.path),
        })}
      />
    </Modal>
  </div>;
}
