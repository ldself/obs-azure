// Typed API functions for the Phase 1 user-registry and grant endpoints
// (Build Plan v1.2 §4.1.3). Thin wrappers over the HTTP client (RULE 3).

import { api } from "./client";
import type {
  CostCenterGrant,
  CostCenterGrantCreate,
  Me,
  RollupGrantCreate,
  User,
  UserCreate,
  UserUpdate,
} from "../types";

export const getMe = (): Promise<Me> => api.get<Me>("/api/v1/me");

export const listUsers = (): Promise<User[]> => api.get<User[]>("/api/v1/users");

export const createUser = (body: UserCreate): Promise<User> =>
  api.post<User>("/api/v1/users", body);

export const updateUser = (userId: string, body: UserUpdate): Promise<User> =>
  api.patch<User>(`/api/v1/users/${encodeURIComponent(userId)}`, body);

export const listGrants = (userId: string): Promise<CostCenterGrant[]> =>
  api.get<CostCenterGrant[]>(`/api/v1/users/${encodeURIComponent(userId)}/grants`);

export const addGrant = (userId: string, body: CostCenterGrantCreate): Promise<CostCenterGrant> =>
  api.post<CostCenterGrant>(`/api/v1/users/${encodeURIComponent(userId)}/grants`, body);

export const removeGrant = (userId: string, costCenterId: string): Promise<void> =>
  api.del<void>(
    `/api/v1/users/${encodeURIComponent(userId)}/grants/${encodeURIComponent(costCenterId)}`,
  );

export const addRollupGrant = (userId: string, body: RollupGrantCreate): Promise<{ user_id: string; rollup_id: string }> =>
  api.post<{ user_id: string; rollup_id: string }>(
    `/api/v1/users/${encodeURIComponent(userId)}/rollup-grants`,
    body,
  );
