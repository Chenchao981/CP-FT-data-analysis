export type CapabilityStage = "CP" | "FT";

export interface FactoryOption {
  value: string;
  label: string;
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

export function isFormalFactory(stage: CapabilityStage, factoryCode: string): boolean {
  const normalized = factoryCode.trim().toLowerCase();
  return formalFactoryOptions[stage].some((factory) => factory.value === normalized);
}
