export type CapabilityStage = "CP" | "FT";

export interface FactoryOption {
  value: string;
  label: string;
}

export interface FactoryCapability extends FactoryOption {
  stage: CapabilityStage;
  route: "FORMAL_IMPORT" | "PENDING_FORMAL_IMPORT";
  scope: string;
}

export const factoryNames: Record<string, string> = {
  huahong: "华虹",
  jetech: "Jetech",
  lion: "立昂微",
  guoyu: "国宇 FRD",
  riyuexin: "日月新",
  riyueguang: "日月光",
  ase: "日月光",
  dianji: "电基",
  jijia: "集佳",
  jiequn: "杰群",
};

export const formalFactoryOptions: Record<CapabilityStage, FactoryOption[]> = {
  CP: [
    { value: "huahong", label: "华虹" },
    { value: "jetech", label: "Jetech" },
    { value: "lion", label: "立昂微" },
  ],
  FT: [
    { value: "riyuexin", label: "日月新" },
    { value: "riyueguang", label: "日月光" },
  ],
};

export const factoryInputs: Record<string, { accept: string; hint: string }> = {
  huahong: { accept: ".zip,.7z,.txt", hint: "华虹支持 ZIP、7Z 或保留目录身份的 TXT 数据。" },
  jetech: { accept: ".zip,.xls,.xlsx", hint: "Jetech 支持 ZIP、XLS、XLSX。" },
  lion: { accept: ".zip,.xls,.xlsx", hint: "立昂微支持 ZIP、XLS、XLSX；系统严格区分已验收格式。" },
  riyuexin: { accept: ".xlsx", hint: "日月新当前正式支持已验收的 DC XLSX；未知布局会停止处理。" },
  riyueguang: { accept: ".xlsx", hint: "日月光当前正式支持已验收的 DC XLSX；请勿混入 DVDS、RG、HTDC 或 TF。" },
};

export const generalCapabilities: FactoryCapability[] = [
  ...formalFactoryOptions.CP.map((item) => ({ ...item, stage: "CP" as const, route: "FORMAL_IMPORT" as const, scope: "Cleaner → Canonical → Dataset → 分析" })),
  ...formalFactoryOptions.FT.map((item) => ({ ...item, stage: "FT" as const, route: "FORMAL_IMPORT" as const, scope: "DC XLSX → Canonical → Dataset → 参数分析" })),
  { value: "dianji", label: "电基", stage: "FT", route: "PENDING_FORMAL_IMPORT", scope: "旧 Cleaner 可用，待独立 Route A 对账" },
  { value: "jijia", label: "集佳", stage: "FT", route: "PENDING_FORMAL_IMPORT", scope: "旧 Cleaner 可用，待独立 Route A 对账" },
  { value: "jiequn", label: "杰群", stage: "FT", route: "PENDING_FORMAL_IMPORT", scope: "Quick PAT 已开放，正式入库待独立对账" },
];

export const customCapabilities = [
  {
    code: "GUOYU_FRD_EXCEL",
    name: "国宇 FRD Excel 清洗",
    ownerScope: "特定小组 Excel 快速清洗",
    currentRuntime: "原 CP 桌面工具可用",
    boundary: "不作为通用 CP Die 明细入口；历史数据和 Cleaner 保留",
  },
  {
    code: "LION_DIE_COUNT",
    name: "立昂微-管芯数",
    ownerScope: "特定月报的 Wafer 管芯数汇总",
    currentRuntime: "原 CP 桌面工具可用",
    boundary: "独立结果，不写入通用 CP Measurement",
  },
];

export function isFormalFactory(stage: CapabilityStage, factoryCode: string): boolean {
  const normalized = factoryCode.trim().toLowerCase();
  return formalFactoryOptions[stage].some((factory) => factory.value === normalized);
}
