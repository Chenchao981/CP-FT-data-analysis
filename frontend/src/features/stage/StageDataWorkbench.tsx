import { BarChartOutlined, CloudServerOutlined, CloudUploadOutlined, DownloadOutlined, FileSearchOutlined, FilterOutlined, FolderOpenOutlined, FormOutlined, InfoCircleOutlined, LeftOutlined, RedoOutlined, ReloadOutlined, UnorderedListOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Card, Checkbox, Col, Empty, Form, Input, Modal, Popconfirm, Radio, Row, Select, Space, Statistic, Table, Tabs, Tag, Typography, Upload, message } from "antd";
import type { UploadFile } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useMemo, useRef, useState } from "react";
import { BusinessDomain, FormalSourceDirectory, StageScope, TestStage, downloadStageUploadFile, listFormalSourceDirectories, listFormalSourceRoots, listStageResultsPage, listStageUploadsPage, previewFormalSourceManifest, reprocessStageBatch, StageResultRow, StageUploadRow, uploadStageData } from "../../api/stageData";
import {
  formatUtcDateTime,
  shanghaiLocalInputToUtc,
  utcToShanghaiLocalInput,
} from "../../utils/dateTime";
import { MetricStrip } from "../../components/MetricStrip";
import { useAuth } from "../auth/AuthContext";
import { factoryInputs, factoryNames, formalFactoryOptions, isFormalFactory } from "../capabilities/capabilityCatalog";
import { LotEnrichmentModal } from "./LotEnrichmentModal";

const statusColor: Record<string, string> = { RECEIVED: "blue", QUEUED: "gold", PROCESSING: "processing", PROCESSED: "success", NEEDS_INPUT: "orange", FAILED: "error" };
const statusName: Record<string, string> = { RECEIVED: "已接收", QUEUED: "排队中", PROCESSING: "处理中", PROCESSED: "已处理", NEEDS_INPUT: "待补录", FAILED: "失败", CANCELLED: "已取消", ARCHIVED: "已归档" };
const activeUploadStatuses = new Set(["RECEIVED", "QUEUED", "PROCESSING"]);
const stageDescription: Record<TestStage, string> = {
  CP: "选择晶圆厂并上传对应CP源文件后，系统自动调用该厂现有清洗程序并形成Wafer分析数据。",
  FT: "选择日月新、日月光或电基并提交对应已验收源文件，系统按独立厂家合同严格校验后形成产品/Lot分析数据。",
};
const visibilityDescription: Record<StageScope, { message: string; description: string }> = {
  ALL: {
    message: "CP/FT 按测试阶段统一使用",
    description: "用户无需选择工程、量产或工厂菜单；待 SAP 订单关联接入后，再由批次号结合量产单和工厂订单信息自动判定。",
  },
  ENGINEERING: {
    message: "工程数据仅上传人本人可见",
    description: "工程上传记录、清洗结果和分析均按上传人隔离；下载、补录和重新处理等动作由服务端逐条授权。",
  },
  PRODUCTION: {
    message: "量产正式结果面向全员共享查询",
    description: "不同人员可以重复上传并创建彼此独立的 Batch 和分析结果；原始文件下载、补录和重新处理仍由服务端逐条授权。",
  },
};
const size = (value: number) => value < 1024 * 1024 ? `${(value / 1024).toFixed(1)} KB` : `${(value / 1024 / 1024).toFixed(2)} MB`;
const needsLotInput = (row: StageUploadRow) => row.status === "NEEDS_INPUT" || row.action_required === "LOT_ID";

export interface StageDataWorkbenchProps {
  businessDomain: StageScope;
  testStage: TestStage;
  searchParams?: URLSearchParams;
  onSearchParamsChange?: (params: URLSearchParams) => void;
  onOpenAnalytics?: (datasetId: number, versionNo: number) => void;
  onOpenJob?: (jobId: number) => void;
}

interface StageFilterValues {
  factory_code?: string;
  upload_status?: string;
  result_status?: string;
  product_name?: string;
  lot_id?: string;
  from_utc?: string;
  to_utc?: string;
}

interface StageFilterFormValues {
  factory_code?: string;
  upload_status?: string;
  result_status?: string;
  product_name?: string;
  lot_id?: string;
  from_local?: string;
  to_local?: string;
}

const stageFilterKeys = ["factory_code", "upload_status", "result_status", "product_name", "lot_id", "from_utc", "to_utc"] as const;
const stageBusinessFilterKeys = ["factory_code", "upload_status", "result_status", "product_name", "lot_id"] as const;

const positiveQueryInt = (params: URLSearchParams, key: string, fallback: number, maximum?: number) => {
  const parsed = Number(params.get(key));
  if (!Number.isInteger(parsed) || parsed <= 0) return fallback;
  return maximum == null ? parsed : Math.min(parsed, maximum);
};

const stageFiltersFromSearch = (params: URLSearchParams): StageFilterValues => Object.fromEntries(
  stageFilterKeys.flatMap((key) => {
    const value = params.get(key)?.trim();
    return value ? [[key, value]] : [];
  }),
) as StageFilterValues;

export function StageDataWorkbench({ businessDomain, testStage, searchParams, onSearchParamsChange, onOpenAnalytics, onOpenJob }: StageDataWorkbenchProps) {
  const { user, can } = useAuth();
  const operationalDomain: BusinessDomain = businessDomain === "ALL" ? "ENGINEERING" : businessDomain;
  const [open, setOpen] = useState(false);
  const [lotTarget, setLotTarget] = useState<StageUploadRow>();
  const [failureRow, setFailureRow] = useState<StageUploadRow>();
  const [files, setFiles] = useState<UploadFile[]>([]);
  const [inputMode, setInputMode] = useState<"UPLOAD" | "CATALOG">("UPLOAD");
  const [sourceRootCode, setSourceRootCode] = useState<string>();
  const [sourceRelativePath, setSourceRelativePath] = useState(".");
  const [confirmedManifestSha, setConfirmedManifestSha] = useState<string>();
  const [downloadError, setDownloadError] = useState<string>();
  const [localSearchParams, setLocalSearchParams] = useState(() => new URLSearchParams());
  const searchKey = (searchParams ?? localSearchParams).toString();
  const currentSearchParams = useMemo(() => new URLSearchParams(searchKey), [searchKey]);
  const filters = useMemo(() => stageFiltersFromSearch(currentSearchParams), [currentSearchParams]);
  const [advancedFiltersOpen, setAdvancedFiltersOpen] = useState(() => Boolean(filters.upload_status || filters.from_utc || filters.to_utc));
  useEffect(() => {
    if (filters.upload_status || filters.from_utc || filters.to_utc) setAdvancedFiltersOpen(true);
  }, [filters.from_utc, filters.to_utc, filters.upload_status]);
  const uploadPage = useMemo(() => ({
    page: positiveQueryInt(currentSearchParams, "upload_page", 1),
    pageSize: positiveQueryInt(currentSearchParams, "upload_page_size", 20, 100),
  }), [currentSearchParams]);
  const resultPage = useMemo(() => ({
    page: positiveQueryInt(currentSearchParams, "result_page", 1),
    pageSize: positiveQueryInt(currentSearchParams, "result_page_size", 20, 100),
  }), [currentSearchParams]);
  const activeTab = currentSearchParams.get("tab") === "result" ? "result" : "source";
  const updateSearchParams = (mutate: (next: URLSearchParams) => void) => {
    const next = new URLSearchParams(currentSearchParams);
    mutate(next);
    if (onSearchParamsChange) onSearchParamsChange(next);
    else setLocalSearchParams(next);
  };
  const [form] = Form.useForm<{ factory_code: string; remark?: string }>();
  const [filterForm] = Form.useForm<StageFilterFormValues>();
  const defaultFactory = formalFactoryOptions[testStage][0].value;
  const watchedFactory = Form.useWatch("factory_code", form);
  const selectedFactory = watchedFactory && isFormalFactory(testStage, watchedFactory) ? watchedFactory : defaultFactory;
  const selectedInput = factoryInputs[selectedFactory];
  const [messageApi, contextHolder] = message.useMessage();
  const queryClient = useQueryClient();
  const scopeKey = ["stage", businessDomain, testStage];
  const previousActiveBatchIds = useRef<Set<number>>(new Set());
  const previousPollingScope = useRef(`${businessDomain}:${testStage}`);
  useEffect(() => {
    setOpen(false);
    setLotTarget(undefined);
    setFailureRow(undefined);
    setFiles([]);
    setInputMode("UPLOAD");
    setSourceRootCode(undefined);
    setSourceRelativePath(".");
    setConfirmedManifestSha(undefined);
    setDownloadError(undefined);
    if (!searchParams) setLocalSearchParams(new URLSearchParams());
    form.resetFields();
    form.setFieldsValue({ factory_code: defaultFactory, remark: undefined });
  }, [businessDomain, defaultFactory, form, searchParams, testStage]);
  useEffect(() => {
    filterForm.resetFields();
    filterForm.setFieldsValue({
      factory_code: filters.factory_code,
      upload_status: filters.upload_status,
      result_status: filters.result_status,
      product_name: filters.product_name,
      lot_id: filters.lot_id,
      from_local: utcToShanghaiLocalInput(filters.from_utc),
      to_local: utcToShanghaiLocalInput(filters.to_utc),
    });
  }, [filterForm, filters]);
  const uploads = useQuery({
    queryKey: [...scopeKey, "uploads-page", uploadPage, filters],
    queryFn: () => listStageUploadsPage(businessDomain, testStage, {
      page: uploadPage.page,
      page_size: uploadPage.pageSize,
      factory_code: filters.factory_code,
      status: filters.upload_status,
      product_name: filters.product_name,
      lot_id: filters.lot_id,
      from_utc: filters.from_utc,
      to_utc: filters.to_utc,
    }),
    refetchInterval: (query) => (query.state.data?.items ?? []).some((row) => activeUploadStatuses.has(row.status)) ? 3000 : false,
  });
  const results = useQuery({
    queryKey: [...scopeKey, "results-page", resultPage, filters],
    queryFn: () => listStageResultsPage(businessDomain, testStage, {
      page: resultPage.page,
      page_size: resultPage.pageSize,
      factory_code: filters.factory_code,
      status: filters.result_status,
      product_name: filters.product_name,
      lot_id: filters.lot_id,
      from_utc: filters.from_utc,
      to_utc: filters.to_utc,
    }),
  });
  useEffect(() => {
    setSourceRootCode(undefined);
    setSourceRelativePath(".");
    setConfirmedManifestSha(undefined);
  }, [businessDomain, selectedFactory, testStage]);
  const sourceRoots = useQuery({
    queryKey: [...scopeKey, "formal-source-roots", selectedFactory],
    queryFn: () => listFormalSourceRoots(operationalDomain, testStage, selectedFactory),
    enabled: open && inputMode === "CATALOG",
  });
  useEffect(() => {
    if (!sourceRoots.data?.length) return;
    if (!sourceRootCode || !sourceRoots.data.some((item) => item.code === sourceRootCode)) {
      const first = sourceRoots.data.find((item) => item.available) ?? sourceRoots.data[0];
      setSourceRootCode(first.code);
      setSourceRelativePath(".");
    }
  }, [sourceRootCode, sourceRoots.data]);
  const sourceDirectories = useQuery({
    queryKey: [...scopeKey, "formal-source-directories", selectedFactory, sourceRootCode, sourceRelativePath],
    queryFn: () => listFormalSourceDirectories(operationalDomain, testStage, selectedFactory, sourceRootCode!, sourceRelativePath),
    enabled: open && inputMode === "CATALOG" && Boolean(sourceRootCode),
  });
  const selectedCatalogPath = sourceDirectories.data?.current_relative_path ?? sourceRelativePath;
  const sourceManifest = useQuery({
    queryKey: [...scopeKey, "formal-source-manifest", selectedFactory, sourceRootCode, selectedCatalogPath],
    queryFn: () => previewFormalSourceManifest(
      operationalDomain,
      testStage,
      selectedFactory,
      sourceRootCode!,
      selectedCatalogPath,
    ),
    enabled: open
      && inputMode === "CATALOG"
      && Boolean(sourceRootCode)
      && sourceDirectories.isSuccess,
  });
  useEffect(() => {
    const pollingScope = `${businessDomain}:${testStage}`;
    const currentActiveBatchIds = new Set(
      (uploads.data?.items ?? [])
        .filter((row) => activeUploadStatuses.has(row.status))
        .map((row) => row.import_batch_id),
    );
    if (previousPollingScope.current !== pollingScope) {
      previousPollingScope.current = pollingScope;
      previousActiveBatchIds.current = currentActiveBatchIds;
      return;
    }
    const batchReachedTerminalStatus = [...previousActiveBatchIds.current]
      .some((batchId) => !currentActiveBatchIds.has(batchId));
    previousActiveBatchIds.current = currentActiveBatchIds;
    if (batchReachedTerminalStatus) {
      void queryClient.invalidateQueries({ queryKey: [...scopeKey, "results-page"] });
    }
  }, [businessDomain, queryClient, testStage, uploads.data]);
  const refresh = async () => Promise.all([uploads.refetch(), results.refetch()]);
  const mutation = useMutation({
    mutationFn: async (values: { factory_code: string; remark?: string }) => {
      const nativeFiles = files.flatMap((item) => item.originFileObj ? [item.originFileObj as File] : []);
      if (inputMode === "UPLOAD" && !nativeFiles.length) throw new Error(`请选择${testStage}源文件`);
      if (inputMode === "CATALOG" && (!sourceRootCode || !sourceDirectories.data)) throw new Error("请选择可用的受控数据源目录");
      if (inputMode === "CATALOG" && (!sourceManifest.data || confirmedManifestSha !== sourceManifest.data.sha)) {
        throw new Error("请先核对并确认当前目录的正式入库清单");
      }
      return uploadStageData(
        operationalDomain,
        testStage,
        inputMode === "UPLOAD" ? nativeFiles : [],
        values.factory_code,
        values.remark,
        inputMode === "CATALOG" ? sourceRootCode : undefined,
        inputMode === "CATALOG" ? sourceManifest.data?.relative_path ?? selectedCatalogPath : undefined,
        inputMode === "CATALOG" ? sourceManifest.data?.mode : undefined,
        inputMode === "CATALOG" ? sourceManifest.data?.sha : undefined,
      );
    },
    onSuccess: async (data) => { messageApi.success(`批次 ${data.import_batch_id} 已进入后台清洗队列（任务 ${data.job_id}；Cleaner ${data.cleaner_release.cleaner_code} ${data.cleaner_release.cleaner_version}）`); setOpen(false); setFiles([]); setInputMode("UPLOAD"); setSourceRootCode(undefined); setSourceRelativePath("."); setConfirmedManifestSha(undefined); form.resetFields(); onOpenJob?.(data.job_id); await queryClient.invalidateQueries({ queryKey: scopeKey }); },
    onError: async (error) => {
      messageApi.error(error.message);
      if (inputMode === "CATALOG") {
        setConfirmedManifestSha(undefined);
        await Promise.all([sourceDirectories.refetch(), sourceManifest.refetch()]);
      }
    },
  });
  const reprocessMutation = useMutation({
    mutationFn: (row: Pick<StageUploadRow | StageResultRow, "import_batch_id" | "business_domain">) => reprocessStageBatch(row.business_domain ?? operationalDomain, testStage, row.import_batch_id),
    onSuccess: async (data) => { messageApi.success(`批次 ${data.import_batch_id} 已进入重新处理队列`); await queryClient.invalidateQueries({ queryKey: scopeKey }); },
    onError: (error) => messageApi.error(error.message),
  });
  const batchRows = useMemo(() => {
    const firstByBatch = new Map<number, StageUploadRow>();
    for (const row of uploads.data?.items ?? []) {
      const current = firstByBatch.get(row.import_batch_id);
      if (!current || row.sequence_no < current.sequence_no) firstByBatch.set(row.import_batch_id, row);
    }
    return [...firstByBatch.values()];
  }, [uploads.data?.items]);
  const handleDownload = async (row: StageUploadRow) => {
    setDownloadError(undefined);
    try {
      await downloadStageUploadFile(
        row.business_domain ?? operationalDomain,
        testStage,
        row.import_batch_id,
        row.receipt_id,
        row.original_file_name,
      );
    } catch (error) {
      const detail = error instanceof Error ? error.message : "未知错误";
      setDownloadError(detail);
      messageApi.error("源文件下载失败");
    }
  };
  const firstSequenceByBatch = useMemo(
    () => new Map(batchRows.map((row) => [row.import_batch_id, row.sequence_no])),
    [batchRows],
  );
  const sourceDirectoryColumns: ColumnsType<FormalSourceDirectory> = [
    {
      title: "目录",
      dataIndex: "name",
      render: (name, row) => <Button type="link" icon={<FolderOpenOutlined />} onClick={() => { setSourceRelativePath(row.relative_path); setConfirmedManifestSha(undefined); }}>{name}</Button>,
    },
    { title: "当前层源文件", dataIndex: "direct_file_count", width: 120 },
    { title: "当前层大小", dataIndex: "direct_total_bytes", width: 120, render: size },
  ];
  const uploadColumns: ColumnsType<StageUploadRow> = [
    { title: "批次编号", dataIndex: "import_batch_id", width: 95, fixed: "left" },
    { title: "当前任务", dataIndex: "latest_job_id", width: 115, render: (v) => v ? <Button type="link" size="small" icon={<UnorderedListOutlined />} onClick={() => onOpenJob?.(v)}>Job #{v}</Button> : "—" },
    { title: "SEQ", dataIndex: "sequence_no", width: 70 },
    { title: "源文件名称", dataIndex: "original_file_name", width: 300, ellipsis: true },
    { title: "扩展名", dataIndex: "extension", width: 80, render: (v) => v.toUpperCase() },
    { title: "大小", dataIndex: "size_bytes", width: 100, render: size },
    { title: testStage === "CP" ? "晶圆厂" : "封测厂", dataIndex: "factory_code", width: 100, render: (v) => factoryNames[String(v).toLowerCase()] ?? v },
    { title: "上传时间", dataIndex: "upload_time_utc", width: 175, render: formatUtcDateTime },
    { title: "队列等待", dataIndex: "queue_age_seconds", width: 105, render: (value: number | null | undefined) => value == null ? "—" : `${value} 秒` },
    { title: "完成时间", dataIndex: "completion_time_utc", width: 175, render: formatUtcDateTime },
    { title: "上传账号", dataIndex: "uploader_login", width: 120 },
    { title: "上传人", dataIndex: "uploader_name", width: 110 },
    { title: "重复来源", dataIndex: "is_duplicate_receipt", width: 145, render: (value: boolean) => value ? <Tag color="warning">相同内容已上传</Tag> : <Tag>首次接收</Tag> },
    { title: "状态", dataIndex: "status", width: 100, fixed: "right", render: (_, row) => {
      const displayStatus = needsLotInput(row) ? "NEEDS_INPUT" : row.status;
      return <Tag color={statusColor[displayStatus]}>{statusName[displayStatus] ?? displayStatus}</Tag>;
    } },
    { title: "操作", key: "actions", width: 310, fixed: "right", render: (_, row) => {
      const firstRow = firstSequenceByBatch.get(row.import_batch_id) === row.sequence_no;
      const awaitingLot = needsLotInput(row);
      const ordinaryFailure = row.status === "FAILED" && !awaitingLot;
      return <Space size={0} wrap>
        {row.can_download_source && <Button type="link" size="small" icon={<DownloadOutlined />} onClick={() => void handleDownload(row)}>下载</Button>}
        {firstRow && awaitingLot && row.can_manage && <Button type="link" size="small" icon={<FormOutlined />} onClick={() => setLotTarget(row)}>补录批次号</Button>}
        {firstRow && ordinaryFailure && <Button type="link" size="small" icon={<InfoCircleOutlined />} onClick={() => setFailureRow(row)}>失败详情</Button>}
        {firstRow && ordinaryFailure && row.can_manage && isFormalFactory(testStage, row.factory_code) && <Popconfirm title="重新处理该批次？" description="系统会使用现有源文件重新运行清洗程序。" onConfirm={() => reprocessMutation.mutate(row)}><Button type="link" size="small" icon={<RedoOutlined />} loading={reprocessMutation.isPending && reprocessMutation.variables?.import_batch_id === row.import_batch_id}>重新处理</Button></Popconfirm>}
      </Space>;
    } },
  ];
  const resultColumns: ColumnsType<StageResultRow> = [
    { title: "Batch", dataIndex: "import_batch_id", width: 95, fixed: "left", render: (value) => `#${value}` },
    { title: "产品名称", dataIndex: "product_name", width: 180, fixed: "left", render: (v) => v || "—" },
    { title: "批号", dataIndex: "lot_id", width: 160, render: (v) => v || "—" },
    ...(testStage === "CP" ? [{ title: "晶圆数", dataIndex: "wafer_count", width: 90 }] : []),
    { title: testStage === "CP" ? "晶圆厂" : "封测厂", dataIndex: "factory_code", width: 100, render: (v) => factoryNames[String(v).toLowerCase()] ?? v },
    { title: "测试项", dataIndex: "test_item_count", width: 90 },
    { title: "总数", dataIndex: "unit_count", width: 105 },
    { title: "良品数", dataIndex: "pass_count", width: 105 },
    { title: "良率", dataIndex: "yield_rate", width: 100, render: (v: number | null) => v == null ? "—" : `${(v * 100).toFixed(2)}%` },
    { title: "状态", dataIndex: "status", width: 100, render: (v) => <Tag color="success">{statusName[v] ?? v}</Tag> },
    { title: "Data Type", dataIndex: "data_type", width: 105 },
    { title: "上传人", key: "uploader", width: 170, ellipsis: true, render: (_, row) => `${row.uploader_name}（${row.uploader_login}）` },
    { title: "处理时间", dataIndex: "created_at_utc", width: 175, render: formatUtcDateTime },
    { title: "操作", key: "actions", width: 270, fixed: "right", render: (_, row) => <Space size={0}>
      {row.dataset_id && row.dataset_version_no && can("DATASET_READ") && <Button type="link" size="small" icon={<BarChartOutlined />} onClick={() => onOpenAnalytics?.(row.dataset_id!, row.dataset_version_no!)}>数据分析</Button>}
      {row.job_id && <Button type="link" size="small" icon={<UnorderedListOutlined />} onClick={() => onOpenJob?.(row.job_id!)}>Job详情</Button>}
      {row.can_manage && isFormalFactory(testStage, row.factory_code) && <Popconfirm title="重新处理该批次？" description="将重跑现有清洗程序并归档旧结果。" onConfirm={() => reprocessMutation.mutate(row)}><Button type="link" size="small" icon={<RedoOutlined />} loading={reprocessMutation.isPending && reprocessMutation.variables?.import_batch_id === row.import_batch_id}>重新处理</Button></Popconfirm>}
    </Space> },
  ];
  const metrics = useMemo(() => ({
    total: uploads.data?.total ?? 0,
    processing: batchRows.filter((row) => activeUploadStatuses.has(row.status)).length,
    processed: results.data?.total ?? 0,
    needsInput: batchRows.filter(needsLotInput).length,
    failed: batchRows.filter((row) => row.status === "FAILED" && !needsLotInput(row)).length,
  }), [batchRows, results.data?.total, uploads.data?.total]);
  const oldestQueueAge = useMemo(() => {
    const ages = (uploads.data?.items ?? [])
      .filter((row) => row.status === "QUEUED" && row.queue_age_seconds != null)
      .map((row) => row.queue_age_seconds!);
    return ages.length ? Math.max(...ages) : undefined;
  }, [uploads.data?.items]);

  return <div className="workbench production-workbench">
    {contextHolder}
    <div className="page-heading"><div><Typography.Text type="secondary">{testStage} 统一数据入口</Typography.Text><Typography.Title level={2}>{testStage}数据</Typography.Title><Typography.Text type="secondary">{stageDescription[testStage]}</Typography.Text></div><Space><Button icon={<ReloadOutlined />} onClick={() => void refresh()}>刷新</Button>{can("TASK_CREATE") && <Button type="primary" icon={<CloudUploadOutlined />} onClick={() => { setFiles([]); setInputMode("UPLOAD"); setSourceRootCode(undefined); setSourceRelativePath("."); setOpen(true); }}>上传数据</Button>}</Space></div>
    <Alert showIcon type="info" className="compact-info-alert" message={visibilityDescription[businessDomain].message} description={visibilityDescription[businessDomain].description} />
    <MetricStrip ariaLabel={`${testStage} 数据处理状态`} items={[
      { label: "查询上传记录", value: metrics.total },
      { label: "当前页处理中", value: metrics.processing, tone: "primary" },
      { label: "查询清洗结果", value: metrics.processed, tone: "success" },
      { label: "当前页待补录", value: metrics.needsInput, tone: metrics.needsInput ? "warning" : "default" },
      { label: "当前页失败", value: metrics.failed, tone: metrics.failed ? "danger" : "default" },
    ]} />
    <Card className="review-filter-card">
      <Form<StageFilterFormValues> form={filterForm} layout="vertical" onFinish={(values) => updateSearchParams((next) => {
        for (const key of stageFilterKeys) next.delete(key);
        for (const key of stageBusinessFilterKeys) {
          const value = values[key]?.trim();
          if (value) next.set(key, value);
        }
        const fromUtc = shanghaiLocalInputToUtc(values.from_local);
        const toUtc = shanghaiLocalInputToUtc(values.to_local);
        if (fromUtc) next.set("from_utc", fromUtc);
        if (toUtc) next.set("to_utc", toUtc);
        next.set("upload_page", "1");
        next.set("result_page", "1");
      })}>
        <Row gutter={[12, 0]}>
          <Col xs={24} sm={12} lg={6}><Form.Item label={testStage === "CP" ? "晶圆厂" : "封测厂"} name="factory_code"><Select allowClear options={formalFactoryOptions[testStage]} /></Form.Item></Col>
          <Col xs={24} sm={12} lg={6}><Form.Item label="产品" name="product_name"><Input allowClear /></Form.Item></Col>
          <Col xs={24} sm={12} lg={6}><Form.Item label="Lot" name="lot_id"><Input allowClear /></Form.Item></Col>
          <Col xs={24} sm={12} lg={6}><Form.Item label="结果状态" name="result_status"><Select allowClear options={["PROCESSED", "FAILED", "ARCHIVED"].map((value) => ({ value, label: statusName[value] }))} /></Form.Item></Col>
        </Row>
        <details className="advanced-filter-details" open={advancedFiltersOpen} onToggle={(event) => setAdvancedFiltersOpen(event.currentTarget.open)}>
          <summary>更多筛选（上传状态、时间范围）</summary>
          <Row gutter={[12, 0]}>
            <Col xs={24} sm={12} lg={6}><Form.Item label="上传状态" name="upload_status"><Select allowClear options={["RECEIVED", "QUEUED", "PROCESSING", "NEEDS_INPUT", "PROCESSED", "FAILED", "CANCELLED"].map((value) => ({ value, label: statusName[value] }))} /></Form.Item></Col>
            <Col xs={24} sm={12} lg={6}><Form.Item label="开始时间（上海，含）" name="from_local"><Input type="datetime-local" allowClear /></Form.Item></Col>
            <Col xs={24} sm={12} lg={6}><Form.Item label="结束时间（上海，不含）" name="to_local"><Input type="datetime-local" allowClear /></Form.Item></Col>
          </Row>
        </details>
        <Space wrap className="filter-actions"><Button type="primary" htmlType="submit" icon={<FilterOutlined />}>服务端检索</Button><Button onClick={() => { setAdvancedFiltersOpen(false); filterForm.resetFields(); updateSearchParams((next) => { for (const key of stageFilterKeys) next.delete(key); next.set("upload_page", "1"); next.set("result_page", "1"); }); }}>清空</Button><Typography.Text type="secondary">服务端按权限、筛选和页码返回，不加载全表。</Typography.Text></Space>
      </Form>
    </Card>
    {oldestQueueAge != null && <Alert type="info" showIcon message="队列等待观测（当前页）" description={`当前页最长已等待 ${oldestQueueAge} 秒。该值不代表 Worker 在线或离线；请由具备 AUDIT_READ 权限的人员在“运行一致性”查看后端运维观测。`} className="review-alert" />}
    {(uploads.isError || results.isError) && <Alert type="error" showIcon message={`${testStage}数据加载失败`} description={(uploads.error ?? results.error)?.message} />}
    {downloadError && <Alert type="error" showIcon closable message="源文件下载失败" description={downloadError} onClose={() => setDownloadError(undefined)} style={{ marginBottom: 16 }} />}
    <Card className="production-table-card"><Tabs activeKey={activeTab} onChange={(tab) => updateSearchParams((next) => { if (tab === "result") next.set("tab", "result"); else next.delete("tab"); })} items={[
      { key: "source", label: "原始文件", children: <Table rowKey={(r) => `${r.import_batch_id}-${r.sequence_no}`} columns={uploadColumns} dataSource={uploads.data?.items ?? []} loading={uploads.isLoading} scroll={{ x: 1980 }} pagination={{ current: uploads.data?.page ?? uploadPage.page, pageSize: uploads.data?.page_size ?? uploadPage.pageSize, total: uploads.data?.total ?? 0, showSizeChanger: true, pageSizeOptions: [10, 20, 50, 100], showTotal: (total) => `共 ${total} 条` }} onChange={(pagination) => updateSearchParams((next) => { next.set("upload_page", String(pagination.current ?? 1)); next.set("upload_page_size", String(pagination.pageSize ?? 20)); })} /> },
      { key: "result", label: "清洗结果", children: <Table rowKey="result_summary_id" columns={resultColumns} dataSource={results.data?.items ?? []} loading={results.isLoading} scroll={{ x: 1720 }} pagination={{ current: results.data?.page ?? resultPage.page, pageSize: results.data?.page_size ?? resultPage.pageSize, total: results.data?.total ?? 0, showSizeChanger: true, pageSizeOptions: [10, 20, 50, 100], showTotal: (total) => `共 ${total} 条` }} onChange={(pagination) => updateSearchParams((next) => { next.set("result_page", String(pagination.current ?? 1)); next.set("result_page_size", String(pagination.pageSize ?? 20)); })} /> },
    ]} /></Card>
    <Modal title={`提交${testStage}数据`} open={open} width={820} onCancel={() => !mutation.isPending && setOpen(false)} onOk={() => form.submit()} okText="提交后台清洗" confirmLoading={mutation.isPending} okButtonProps={{ disabled: inputMode === "CATALOG" && (!sourceManifest.data || confirmedManifestSha !== sourceManifest.data.sha) }} destroyOnHidden>
      <Alert showIcon type="info" message={`上传身份：${user?.display_name}（${user?.login_name}）`} description="系统从当前登录账号自动记录上传人，无需填写。" />
      {businessDomain === "PRODUCTION" && <Alert showIcon type="success" message="量产数据允许重复上传" description="即使文件内容或 Lot 与既有记录相同，本次提交仍会创建独立 Batch，并保留本次上传人与分析结果。" style={{ marginTop: 12 }} />}
      <Form form={form} layout="vertical" initialValues={{ factory_code: defaultFactory }} onFinish={(values) => mutation.mutate(values)} className="cp-upload-form">
        <Form.Item label="业务分类"><Space><Tag color="blue">无需选择（SAP 归类待接入）</Tag><Tag color="cyan">{testStage}数据</Tag></Space></Form.Item>
        <Form.Item label="选择解析工具" name="factory_code" rules={[{ required: true }]}><Select options={formalFactoryOptions[testStage]} /></Form.Item>
        <Form.Item label="数据来源" required>
          <Radio.Group value={inputMode} optionType="button" buttonStyle="solid" onChange={(event) => { setInputMode(event.target.value); setFiles([]); setSourceRootCode(undefined); setSourceRelativePath("."); setConfirmedManifestSha(undefined); }}>
            <Radio.Button value="UPLOAD"><CloudUploadOutlined /> 本机文件上传</Radio.Button>
            <Radio.Button value="CATALOG"><CloudServerOutlined /> 受控服务器目录</Radio.Button>
          </Radio.Group>
        </Form.Item>
        {inputMode === "UPLOAD" ? (
          <Form.Item label={`${testStage}源文件`} required><Upload.Dragger multiple accept={selectedInput.accept} fileList={files} beforeUpload={() => false} onChange={({ fileList }) => setFiles(fileList)}><p className="ant-upload-drag-icon"><FileSearchOutlined /></p><p className="ant-upload-text">点击或拖入{factoryNames[selectedFactory]}{testStage}源文件</p><p className="ant-upload-hint">{selectedInput.hint} 上传后自动复用现有清洗逻辑。</p></Upload.Dragger></Form.Item>
        ) : (
          <Form.Item label="受控服务器目录" required>
            <Card size="small">
              {sourceRoots.isError ? <Alert type="error" showIcon message="受控数据源加载失败" description={sourceRoots.error.message} /> : !sourceRoots.isLoading && !sourceRoots.data?.length ? <Empty description={`管理员尚未为${testStage}/${factoryNames[selectedFactory]}配置正式数据源`} /> : <>
                <Space wrap style={{ marginBottom: 12 }}>
                  <Typography.Text strong>数据源</Typography.Text>
                  <Select value={sourceRootCode} loading={sourceRoots.isLoading} style={{ minWidth: 260 }} options={(sourceRoots.data ?? []).map((item) => ({ value: item.code, label: `${item.name}${item.available ? "" : "（不可用）"}`, disabled: !item.available }))} onChange={(value) => { setSourceRootCode(value); setSourceRelativePath("."); setConfirmedManifestSha(undefined); }} />
                  <Button icon={<LeftOutlined />} disabled={!sourceDirectories.data?.parent_relative_path} onClick={() => { if (sourceDirectories.data?.parent_relative_path != null) { setSourceRelativePath(sourceDirectories.data.parent_relative_path); setConfirmedManifestSha(undefined); } }}>上一级</Button>
                  <Typography.Text code>{sourceDirectories.data?.current_relative_path ?? sourceRelativePath}</Typography.Text>
                </Space>
                {sourceDirectories.isError && <Alert type="error" showIcon message="目录读取失败" description={sourceDirectories.error.message} />}
                <Table rowKey="relative_path" size="small" loading={sourceDirectories.isLoading} columns={sourceDirectoryColumns} dataSource={sourceDirectories.data?.directories ?? []} pagination={false} locale={{ emptyText: "当前目录没有子目录，可直接提交当前目录。" }} />
                <div style={{ marginTop: 12 }}>
                  {sourceManifest.isError ? <Alert type="error" showIcon message="正式入库清单加载失败" description={sourceManifest.error.message} /> : (
                    <Card size="small" loading={sourceManifest.isLoading}>
                      {sourceManifest.data ? <>
                        <Row gutter={[12, 12]}>
                          <Col xs={24} sm={8}><Statistic title="扫描范围" value={sourceManifest.data.recursive ? "当前目录及全部子目录" : "仅当前目录"} /></Col>
                          <Col xs={12} sm={8}><Statistic title="源文件数" value={sourceManifest.data.file_count} /></Col>
                          <Col xs={12} sm={8}><Statistic title="源数据大小" value={size(sourceManifest.data.total_bytes)} /></Col>
                        </Row>
                        <Typography.Paragraph style={{ marginTop: 12, marginBottom: 8 }}>
                          <Typography.Text type="secondary">清单指纹（SHA-256）</Typography.Text><br />
                          <Typography.Text code copyable>{sourceManifest.data.sha}</Typography.Text>
                        </Typography.Paragraph>
                        <Checkbox
                          checked={confirmedManifestSha === sourceManifest.data.sha}
                          onChange={(event) => setConfirmedManifestSha(event.target.checked ? sourceManifest.data?.sha : undefined)}
                        >我已核对目录、递归范围、文件数和清单指纹，确认以此清单提交正式入库。</Checkbox>
                      </> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="正在生成正式入库清单" />}
                    </Card>
                  )}
                </div>
                <Typography.Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0 }}>系统不会修改共享目录原文件；提交时会按上述指纹校验后复制只读快照，再由 Worker 二次校验。</Typography.Paragraph>
              </>}
            </Card>
          </Form.Item>
        )}
        <Form.Item label="备注（可选）" name="remark"><Input.TextArea maxLength={500} rows={3} placeholder="可填写本次上传说明" /></Form.Item>
      </Form>
    </Modal>
    <LotEnrichmentModal
      open={lotTarget !== undefined}
      businessDomain={lotTarget?.business_domain ?? operationalDomain}
      testStage={testStage}
      importBatchId={lotTarget?.import_batch_id}
      onClose={() => setLotTarget(undefined)}
      onResolved={async (data) => {
        messageApi.success(`批次号已保存，批次已进入重新处理队列（任务 ${data.job_id}）`);
        setLotTarget(undefined);
        await queryClient.invalidateQueries({ queryKey: scopeKey });
      }}
    />
    <Modal title="处理失败" open={failureRow !== undefined} footer={<Button onClick={() => setFailureRow(undefined)}>关闭</Button>} onCancel={() => setFailureRow(undefined)} destroyOnHidden>
      <Alert
        showIcon
        type="error"
        message="该批次未能完成处理"
        description={failureRow?.error_message || "系统未返回详细原因，请重新处理；如仍失败请联系管理员。"}
      />
      {failureRow?.error_code && <Typography.Paragraph type="secondary" style={{ marginTop: 16 }}>错误类型：{failureRow.error_code}</Typography.Paragraph>}
      {failureRow?.latest_job_id && <Typography.Paragraph type="secondary">当前任务：Job #{failureRow.latest_job_id}</Typography.Paragraph>}
    </Modal>
  </div>;
}
