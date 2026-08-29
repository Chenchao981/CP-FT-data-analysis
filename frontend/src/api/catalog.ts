import { apiRequest } from "./auth";
import type { BusinessDomain, PageResult, TestStage } from "./stageData";

export interface CurrentDatasetRow {
  dataset_id: number;
  dataset_version_id?: number;
  version_no: number;
  import_batch_id: number;
  job_id: number | null;
  processing_run_id: number | null;
  product_name: string | null;
  lot_id: string | null;
  factory_code: string;
  business_domain: BusinessDomain;
  test_stage: TestStage;
  status: string;
  unit_count: number | null;
  pass_count: number | null;
  yield_rate: number | null;
  source_file_count: number;
  processed_at_utc: string;
}

export interface CurrentDatasetRequest {
  page: number;
  page_size: number;
  product_name?: string;
  lot_id?: string;
  factory_code?: string;
  business_domain?: BusinessDomain;
  test_stage?: TestStage;
  status?: string;
  from_utc?: string;
  to_utc?: string;
}

export const listCurrentDatasets = (request: CurrentDatasetRequest) => {
  const query = new URLSearchParams({
    page: String(request.page),
    page_size: String(request.page_size),
  });
  for (const key of ["product_name", "lot_id", "factory_code", "business_domain", "test_stage", "status", "from_utc", "to_utc"] as const) {
    const value = request[key]?.trim();
    if (value) query.set(key, value);
  }
  return apiRequest<PageResult<CurrentDatasetRow>>(
    `/api/v1/catalog/datasets/current?${query}`,
  );
};
