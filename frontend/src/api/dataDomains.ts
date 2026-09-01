import { apiRequest } from "./auth";

export interface DataDomainGrant {
  user_id: number;
  login_name: string;
  display_name: string;
  expires_at_utc: string | null;
  granted_at_utc: string;
  reason: string | null;
}

export interface DataDomain {
  data_domain_id: number;
  domain_code: string;
  domain_name: string;
  test_stage: "CP" | "FT";
  factory_code: string | null;
  active: boolean;
  grant_expires_at_utc: string | null;
  grants: DataDomainGrant[];
}

export interface GrantableUser {
  user_id: number;
  login_name: string;
  display_name: string;
}

export interface CreateDataDomainValues {
  domain_code: string;
  domain_name: string;
  test_stage: "CP" | "FT";
  factory_code?: string;
  active: boolean;
}

export interface UpdateDataDomainValues {
  domain_name: string;
  factory_code?: string;
  active: boolean;
}

export interface GrantDataDomainValues {
  user_id: number;
  expires_at_utc: string | null;
  reason: string;
}

const base = "/api/v1/admin/data-domains";

export const listMyDataDomains = () => apiRequest<DataDomain[]>("/api/v1/data-domains");
export const listAdminDataDomains = () => apiRequest<DataDomain[]>(base);
export const listGrantableUsers = () => apiRequest<GrantableUser[]>(`${base}/grantable-users`);
export const createDataDomain = (values: CreateDataDomainValues) => apiRequest<DataDomain>(base, {
  method: "POST",
  body: JSON.stringify(values),
});
export const updateDataDomain = (dataDomainId: number, values: UpdateDataDomainValues) => apiRequest<DataDomain>(`${base}/${dataDomainId}`, {
  method: "PUT",
  body: JSON.stringify(values),
});
export const grantDataDomain = (dataDomainId: number, values: GrantDataDomainValues) => apiRequest<DataDomainGrant>(`${base}/${dataDomainId}/grants`, {
  method: "POST",
  body: JSON.stringify(values),
});
export const revokeDataDomain = (dataDomainId: number, userId: number) => apiRequest<void>(`${base}/${dataDomainId}/grants/${userId}`, {
  method: "DELETE",
});
