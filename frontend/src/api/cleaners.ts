export interface HuaHongInspection {
  profile_code: string;
  profile_version: string;
  source_file: { name: string; sha256: string };
  identity: {
    business_lot_id: string;
    lot_number: string;
    wafer_number: string;
    program_name: string;
  };
  schema: {
    schema_id: string;
    parameter_count: number;
    parameters: string[];
  };
  quality: {
    status: "PASS";
    row_count: number;
    pass_bin: number;
    pass_count: number;
    yield_rate: number;
    bin_counts: Record<string, number>;
  };
}

interface ErrorEnvelope {
  error?: { message?: string };
}

export async function inspectHuaHongFile(file: File): Promise<HuaHongInspection> {
  const body = new FormData();
  body.append("file", file);
  const response = await fetch("/api/v1/cleaners/huahong/inspect", {
    method: "POST",
    body,
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as ErrorEnvelope;
    throw new Error(payload.error?.message ?? `检查失败（${response.status}）`);
  }
  return response.json() as Promise<HuaHongInspection>;
}
