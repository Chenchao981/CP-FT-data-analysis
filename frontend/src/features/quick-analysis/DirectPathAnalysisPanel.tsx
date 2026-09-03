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
  detail: string;
  available: boolean;
  icon: React.ReactNode;
}

interface FactoryConfig {
  code: string;
  name: string;
  englishName: string;
  sourceHint: string;
  formats: string;
  toolCode: DirectPathToolCode;
  operations: OperationConfig[];
}

const operation = (
  code: OperationCode,
  name: string,
  detail: string,
  icon: React.ReactNode,
  available = false,
): OperationConfig => ({ code, name, detail, icon, available });

const patOperation = operation(
  "PAT",
  "PAT 参数分析",
  "直接解析原始目录，后台计算 PAT",
  <ExperimentOutlined />,
  true,
);

const FACTORIES: Record<WorkbenchStage, FactoryConfig[]> = {
  CP: [
    {
      code: "HUAHONG",
      name: "华虹",
      englishName: "HuaHong",
      sourceHint: "选择 DCP/TXT 数据目录，或单个 ZIP/7z 压缩包",
      formats: "DCP/TXT · ZIP/7z",
      toolCode: "HUAHONG_CP_QUICK_PAT_EXISTING",
      operations: [
        operation("CLEAN", "数据清洗", "输出 cleaned、yield、spec 标准 CSV", <DatabaseOutlined />),
        operation("CHART", "图表分析", "CP Cockpit、良率与参数图表", <BarChartOutlined />),
        patOperation,
      ],
    },
    {
      code: "JETECH",
      name: "积塔",
      englishName: "Jetech",
      sourceHint: "选择积塔 Excel 数据目录、单个 Excel 或 ZIP 压缩包",
      formats: "XLS/XLSX · ZIP",
      toolCode: "JETECH_CP_QUICK_PAT_EXISTING",
      operations: [
        operation("CLEAN", "数据清洗", "输出 cleaned、yield、spec 标准 CSV", <DatabaseOutlined />),
        operation("CHART", "图表分析", "CP Cockpit、良率与参数图表", <BarChartOutlined />),
        patOperation,
      ],
    },
    {
      code: "LION",
      name: "立昂微",
      englishName: "Lion",
      sourceHint: "选择立昂微 CP Excel 数据目录、单个 Excel 或 ZIP 压缩包",
      formats: "XLS/XLSX · ZIP",
      toolCode: "LION_CP_QUICK_PAT_EXISTING",
      operations: [
        operation("CLEAN", "数据清洗", "自动识别已验收的 CP 数据格式", <DatabaseOutlined />),
        operation("CHART", "图表分析", "CP Cockpit 与立昂微专用图表", <BarChartOutlined />),
        patOperation,
        operation("DIE_COUNT", "管芯数汇总", "汇总 NCE品名、LOT、Wafer、PASS、Good Die", <FileExcelOutlined />),
      ],
    },
    {
      code: "GUOYU",
      name: "国宇 FRD",
      englishName: "Guoyu",
      sourceHint: "选择批次/产品 Excel 目录、单个 Excel 或 ZIP 压缩包",
      formats: "XLS/XLSX · ZIP",
      toolCode: "GUOYU_CP_QUICK_PAT_EXISTING",
      operations: [
        operation("CLEAN", "数据清洗", "输出 cleaned、yield、spec 标准 CSV", <DatabaseOutlined />),
        operation("CHART", "图表分析", "CP Cockpit、良率与参数图表", <BarChartOutlined />),
        patOperation,
      ],
    },
  ],
  FT: [
    {
      code: "RIYUEXIN",
      name: "日月新",
      englishName: "Riyuexin",
      sourceHint: "选择产品根目录，或 DC/DVDS/RG 原始 XLSX 目录",
      formats: "DC · DVDS · RG · XLSX · ZIP/7z",
      toolCode: "RIYUEXIN_FT_QUICK_PAT_EXISTING",
      operations: [
        operation("CLEAN", "FT 数据清洗", "DC、DVDS、RG", <DatabaseOutlined />),
        operation("CHART", "FT 散点图", "清洗后查看参数散点与规格", <BarChartOutlined />),
        patOperation,
        operation("SYL_SBL", "SBL & SYL", "选择单个封装厂良率 Excel", <FileExcelOutlined />),
      ],
    },
    {
      code: "JIEQUN",
      name: "杰群",
      englishName: "Jiequn",
      sourceHint: "选择产品根目录、DC/DVDS/RG 目录，或原始 DTA CSV 压缩包",
      formats: "DC-AI · DVDS · RG · CSV · ZIP/7z",
      toolCode: "JIEQUN_FT_QUICK_PAT_EXISTING",
      operations: [
        operation("CLEAN", "FT 数据清洗", "DC-AI 自动识别、DVDS、RG", <DatabaseOutlined />),
        operation("CHART", "FT 散点图", "DC-AI 清洗后查看参数散点", <BarChartOutlined />),
        patOperation,
        operation("SYL_SBL", "SBL & SYL", "选择单个封装厂良率 Excel", <FileExcelOutlined />),
      ],
    },
    {
      code: "RIYUEGUANG",
      name: "日月光",
      englishName: "ASE",
      sourceHint: "选择产品根目录，或 DC/DVDS/RG 原始 XLSX 目录",
      formats: "DC · DVDS · RG · XLSX · ZIP/7z",
      toolCode: "RIYUEGUANG_FT_QUICK_PAT_EXISTING",
      operations: [
        operation("CLEAN", "FT 数据清洗", "按日月光已验收数据格式清洗", <DatabaseOutlined />),
        patOperation,
      ],
    },
    {
      code: "DIANJI",
      name: "电基",
      englishName: "Dianji",
      sourceHint: "选择一种电基 PowerTECH、STS8203 或 TF 原始数据目录",
      formats: "XLS/XLSX/CSV · ZIP/7z",
      toolCode: "DIANJI_FT_QUICK_PAT_EXISTING",
      operations: [
        operation("CLEAN", "FT-ALL 清洗", "自动识别已注册的电基原始格式", <DatabaseOutlined />),
        operation("CHART", "FT 散点图", "FT-ALL 清洗后查看参数散点", <BarChartOutlined />),
        patOperation,
        operation("SYL_SBL", "SBL & SYL", "选择单个封装厂良率 Excel", <FileExcelOutlined />),
      ],
    },
    {
      code: "JIJIA",
      name: "集佳",
      englishName: "Jijia",
      sourceHint: "选择包含集佳 STS8203 原始 CSV 的目录或压缩包",
      formats: "FT-ALL · CSV · ZIP/7z",
      toolCode: "JIJIA_FT_QUICK_PAT_EXISTING",
      operations: [
        operation("CLEAN", "FT-ALL 清洗", "严格解析集佳 STS8203 数据", <DatabaseOutlined />),
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
      messageApi.success(`个人 PAT ${session.analysis_session_id} 已进入后台队列，完成后自动保存到所选输出目录`);
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

  const canRunPat = selected.code === "PAT" && Boolean(preview && currentPaths.output.trim());

  return <div className="personal-tool-workbench">
    {contextHolder}
    <Card className="tool-stage-card">
      <div className="tool-stage-heading">
        <div>
          <Typography.Title level={4}>1. 选择 CP 或 FT 工具</Typography.Title>
          <Typography.Text type="secondary">两个工具台完全分开，不需要选择工程、量产或工厂类型。</Typography.Text>
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

    <Card className="tool-step-card" title="3. 选择输入和输出路径">
      <Alert
        showIcon
        type="info"
        className="compact-info-alert"
        message={factory.sourceHint}
        description="可预览当前 TMS 主机能够访问的本地盘、映射盘和 UNC 路径；目录文件不经过浏览器上传。"
      />
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

        <label htmlFor="quick-output-path">输出路径</label>
        <Space.Compact className="path-input-group">
          <Input
            id="quick-output-path"
            aria-label="输出路径"
            value={currentPaths.output}
            placeholder="选择结果保存文件夹；不存在时由系统创建"
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
          onClick={() => { setSelectedOperation(item.code); setPreview(undefined); }}
        >
          <span className="operation-icon">{item.icon}</span>
          <span><strong>{item.name}</strong><small>{item.detail}</small></span>
          <Tag color={item.available ? "success" : "default"}>{item.available ? "可运行" : "网页待接入"}</Tag>
        </button>)}
      </div>

      {!selected.available && <Alert
        showIcon
        type="warning"
        message={`${factory.name} · ${selected.name} 已在原桌面工具中存在，当前网页后台执行合同尚未接入`}
        description="本页先准确保留厂家、功能和路径交互，不会创建一个无法正确产出结果的假任务。"
      />}

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
          <Typography.Text><strong>输出：</strong><Typography.Text code copyable>{currentPaths.output || "尚未选择"}</Typography.Text></Typography.Text>
        </Space>
        {!currentPaths.output.trim() && <Alert type="warning" showIcon message="请选择输出路径后再开始 PAT" />}
        <List
          size="small"
          header={<Typography.Text strong>将解析的文件{preview.sample_truncated ? "（前 100 个）" : ""}</Typography.Text>}
          dataSource={preview.sample_files}
          renderItem={(item) => <List.Item><Typography.Text code>{item}</Typography.Text></List.Item>}
          className="source-file-list"
        />
      </Card>}

      {selected.code === "PAT" && !preview && <div className="operation-empty-state">
        <ExperimentOutlined />
        <div><strong>PAT 已选定</strong><span>请先在上方选择输入、输出路径，并点击“解析范围”。</span></div>
      </div>}
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
      <Alert
        showIcon
        type="info"
        className="compact-info-alert"
        message={browserPurpose === "INPUT"
          ? `当前厂家支持：${browseMutation.data?.allowed_suffixes.join("、") || "正在读取"}`
          : "双击文件夹继续浏览，确认后结果会直接保存到所选目录"}
        description={browserPurpose === "INPUT"
          ? "可选择一个源文件/压缩包，或进入目标目录后使用当前文件夹。"
          : "输出目录可与输入目录不同；如存在同名 PAT 文件，系统自动追加序号，不覆盖旧结果。"}
      />
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
