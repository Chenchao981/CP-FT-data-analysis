import { apiRequest } from "./auth";
import type { PageResult, TestStage } from "./stageData";

export type ProductCrosswalkStatus = "PENDING" | "APPROVED" | "REJECTED" | "RETIRED";

export interface ProductCrosswalk {
  crosswalk_id: number;
  supplier_id: number;
  supplier_code: string;
  supplier_name: string;
  test_stage: TestStage;
  raw_product_code: string;
  product_id: number;
  tms_product_code: string;
  identity_class: string;
  enterprise_system: string;
  enterprise_key: string | null;
  status: ProductCrosswalkStatus;
  first_observed_at_utc: string;
  last_observed_at_utc: string;
  approved_by_login: string | null;
  approved_at_utc: string | null;
  decision_reason: string | null;
}

export interface ProductCrosswalkRequest {
  page: number;
  page_size: number;
  status?: ProductCrosswalkStatus;
  supplier_code?: string;
  test_stage?: TestStage;
  raw_product_code?: string;
}

export interface ApproveProductCrosswalkPayload {
  enterprise_system: "SAP_B1";
  enterprise_key: string;
  reason: string;
}

export const listProductCrosswalks = (request: ProductCrosswalkRequest) => {
  const query = new URLSearchParams({ page: String(request.page), page_size: String(request.page_size) });
  for (const key of ["status", "supplier_code", "test_stage", "raw_product_code"] as const) {
    const value = request[key]?.trim();
    if (value) query.set(key, value);
  }
  return apiRequest<PageResult<ProductCrosswalk>>(`/api/v1/master-data/product-crosswalks?${query}`);
};

export const approveProductCrosswalk = (crosswalkId: number, payload: ApproveProductCrosswalkPayload) =>
  apiRequest<ProductCrosswalk>(`/api/v1/master-data/product-crosswalks/${crosswalkId}/approve`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const rejectProductCrosswalk = (crosswalkId: number, reason: string) =>
  apiRequest<ProductCrosswalk>(`/api/v1/master-data/product-crosswalks/${crosswalkId}/reject`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
