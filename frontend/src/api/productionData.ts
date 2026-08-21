import { apiRequest } from "./auth";

export interface ProductionUploadRow {
  import_batch_id: number;
  sequence_no: number;
  original_file_name: string;
  extension: string;
  size_bytes: number;
  factory_code: string;
  upload_time_utc: string;
  completion_time_utc: string | null;
  uploader_login: string;
  uploader_name: string;
  status: string;
}

export interface ProductionResultRow {
  result_summary_id: number;
  import_batch_id: number;
  data_name: string;
  product_name: string | null;
  lot_id: string | null;
  wafer_count: number | null;
  factory_code: string;
  test_item_count: number | null;
  unit_count: number | null;
  pass_count: number | null;
  yield_rate: number | null;
  status: string;
  data_type: string;
  created_at_utc: string;
}

export const listCpUploads = () => apiRequest<ProductionUploadRow[]>("/api/v1/production/cp/uploads");
export const listCpResults = () => apiRequest<ProductionResultRow[]>("/api/v1/production/cp/results");
export function uploadCpData(files: File[], factoryCode: string, remark?: string) {
  const body = new FormData();
  files.forEach((file) => body.append("files", file));
  body.append("factory_code", factoryCode);
  if (remark) body.append("remark", remark);
  return apiRequest<{ import_batch_id: number; status: string }>("/api/v1/production/cp/uploads", { method: "POST", body });
}
