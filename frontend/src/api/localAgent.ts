const AGENT_BASE = "http://127.0.0.1:8765/v1";
const AGENT_TOKEN_KEY = "tms_local_agent_token";
const AGENT_RUN_REFERENCE_KEY = `tms_local_agent_run:${new URL(AGENT_BASE).origin}`;
const LOCAL_RUN_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export interface LocalAgentHealth {
  status: "ok";
  service: string;
  version: string;
  pairing_required: boolean;
  pairing_token_ttl_seconds: number;
}

export interface LocalToolCapability {
  tool_code: string;
  display_name: string;
  test_stage: "CP" | "FT";
  factory_code: string;
  analysis_type: string;
  input_contract_version: string;
  output_contract_version: string;
  entrypoint: string;
  allowed_suffixes: string[];
  enabled: boolean;
  disabled_reason: string | null;
  package_sha256: string | null;
  timeout_seconds: number | null;
  max_output_bytes: number | null;
}

export interface LocalSelection {
  selection_id: string;
  source_label: string;
}

export interface LocalManifestPreview {
  mode: "LOCAL_PATH_SIZE_MTIME_V1";
  file_count: number;
  total_bytes: number;
  sha256: string;
  source_label: string;
  tool_code: string;
  allowed_suffixes: string[];
}

export interface LocalRun {
  run_id: string;
  selection_id: string;
  tool_code: string;
  source_label: string;
  status: "QUEUED" | "RUNNING" | "SUCCESS" | "FAILED";
  parameter_count: number | null;
  record_count: number | null;
  elapsed_seconds: number | null;
  error_code: string | null;
  error_message: string | null;
  created_at_utc: string;
  started_at_utc: string | null;
  finished_at_utc: string | null;
}

export interface LocalResultReceipt {
  contract_version: "TMS_LOCAL_RESULT_V1";
  tool_code: string;
  analysis_type: "QUICK_PAT";
  test_stage: "FT";
  factory_code: "JIEQUN";
  release_sha256: string;
  source_label: string;
  manifest: {
    mode: "LOCAL_PATH_SIZE_MTIME_V1";
    sha256: string;
    file_count: number;
    total_bytes: number;
  };
  summary: {
    parameter_count: number;
    record_count: number;
    elapsed_seconds: number;
  };
  result: { filename: string; size_bytes: number; sha256: string };
}

export interface LocalAgentRunReference {
  run_id: string;
  registered_session_id: number | null;
}

export const storedLocalAgentToken = () => (
  typeof sessionStorage === "undefined" ? null : sessionStorage.getItem(AGENT_TOKEN_KEY)
);

export const saveLocalAgentToken = (token: string) => {
  if (typeof sessionStorage !== "undefined") sessionStorage.setItem(AGENT_TOKEN_KEY, token.trim());
};

export const clearLocalAgentToken = () => {
  if (typeof sessionStorage !== "undefined") sessionStorage.removeItem(AGENT_TOKEN_KEY);
};

export const storedLocalAgentRunReference = (): LocalAgentRunReference | null => {
  if (typeof localStorage === "undefined") return null;
  const raw = localStorage.getItem(AGENT_RUN_REFERENCE_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<LocalAgentRunReference>;
    if (
      typeof parsed.run_id !== "string"
      || !LOCAL_RUN_ID_PATTERN.test(parsed.run_id)
      || !(
        parsed.registered_session_id == null
        || (Number.isInteger(parsed.registered_session_id) && parsed.registered_session_id > 0)
      )
    ) {
      throw new Error("invalid Local Agent run reference");
    }
    return {
      run_id: parsed.run_id.toLowerCase(),
      registered_session_id: parsed.registered_session_id ?? null,
    };
  } catch {
    localStorage.removeItem(AGENT_RUN_REFERENCE_KEY);
    return null;
  }
};

export const saveLocalAgentRunReference = (
  runId: string,
  registeredSessionId: number | null = null,
) => {
  const normalized = runId.trim().toLowerCase();
  if (!LOCAL_RUN_ID_PATTERN.test(normalized)) {
    throw new Error("Local Agent 返回了无效的任务编号");
  }
  if (
    registeredSessionId != null
    && (!Number.isInteger(registeredSessionId) || registeredSessionId < 1)
  ) {
    throw new Error("Local Agent 任务的登记会话编号无效");
  }
  if (typeof localStorage !== "undefined") {
    localStorage.setItem(AGENT_RUN_REFERENCE_KEY, JSON.stringify({
      run_id: normalized,
      registered_session_id: registeredSessionId,
    } satisfies LocalAgentRunReference));
  }
};

export const clearLocalAgentRunReference = (expectedRunId?: string) => {
  if (typeof localStorage === "undefined") return;
  if (expectedRunId) {
    const current = storedLocalAgentRunReference();
    if (current && current.run_id !== expectedRunId.trim().toLowerCase()) return;
  }
  localStorage.removeItem(AGENT_RUN_REFERENCE_KEY);
};

export const clearLocalAgentBrowserState = () => {
  clearLocalAgentToken();
  clearLocalAgentRunReference();
};

async function localAgentRequest<T>(path: string, init: RequestInit = {}, token?: string): Promise<T> {
  const headers = new Headers(init.headers);
  const resolvedToken = token?.trim() || storedLocalAgentToken();
  if (resolvedToken) headers.set("X-TMS-Agent-Token", resolvedToken);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  let response: Response;
  try {
    response = await fetch(`${AGENT_BASE}${path}`, { ...init, headers, credentials: "omit" });
  } catch (error) {
    throw new Error("未连接到本机 TMS Agent，请先启动 Agent 并确认端口 8765 可用。", { cause: error });
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { error?: { message?: string } } | null;
    throw new Error(payload?.error?.message ?? `本机 Agent 请求失败（${response.status}）`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const getLocalAgentHealth = () => localAgentRequest<LocalAgentHealth>("/health");

export const listLocalAgentTools = async (token?: string) => {
  const payload = await localAgentRequest<{ tools: LocalToolCapability[] }>("/tools", {}, token);
  return payload.tools;
};

export const selectLocalFolder = () => localAgentRequest<LocalSelection>("/select-folder", {
  method: "POST",
  body: JSON.stringify({}),
});

export const previewLocalSelection = (selectionId: string, toolCode: string) => (
  localAgentRequest<LocalManifestPreview>(`/selections/${encodeURIComponent(selectionId)}/preview`, {
    method: "POST",
    body: JSON.stringify({ tool_code: toolCode }),
  })
);

export const runLocalSelection = (
  selectionId: string,
  toolCode: string,
  confirmedManifestSha256: string,
) => localAgentRequest<{ run_id: string; status: "QUEUED" }>(`/selections/${encodeURIComponent(selectionId)}/runs`, {
  method: "POST",
  body: JSON.stringify({
    tool_code: toolCode,
    confirmed_manifest_sha256: confirmedManifestSha256,
  }),
});

export const getLocalRun = (runId: string) => (
  localAgentRequest<LocalRun>(`/runs/${encodeURIComponent(runId)}`)
);

export const getLocalRunReceipt = (runId: string) => (
  localAgentRequest<LocalResultReceipt>(`/runs/${encodeURIComponent(runId)}/receipt`)
);

export const deleteLocalRun = (runId: string) => (
  localAgentRequest<void>(`/runs/${encodeURIComponent(runId)}`, { method: "DELETE" })
);

export async function getLocalRunResult(runId: string): Promise<Blob> {
  const token = storedLocalAgentToken();
  let response: Response;
  try {
    response = await fetch(`${AGENT_BASE}/runs/${encodeURIComponent(runId)}/result`, {
      headers: token ? { "X-TMS-Agent-Token": token } : {},
      credentials: "omit",
    });
  } catch (error) {
    throw new Error("无法从本机 Agent 读取结果文件。", { cause: error });
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { error?: { message?: string } } | null;
    throw new Error(payload?.error?.message ?? `本机结果读取失败（${response.status}）`);
  }
  return response.blob();
}
