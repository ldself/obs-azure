// MSW handlers for the Phase 1 API (Local Dev Spec v1.1 §10.3). One handler per
// endpoint the SPA calls, returning realistic data matching the obs.* schemas, so
// the frontend (and its acceptance criteria) can be exercised without a running
// backend. Activated by VITE_API_MOCK=true. Keep these in sync with the API
// contract (frontend-conventions-reviewer check).

import { http, HttpResponse } from "msw";

import type { CostCenterGrant, User } from "../types";

const me: User = {
  user_id: "dev-admin-001",
  display_name: "Local Admin",
  email: "admin@obs.local",
  is_active: true,
  is_administrator: true,
  is_system_modeler: false,
  is_report_developer: false,
  is_finance_reviewer: false,
};

const users: User[] = [
  me,
  {
    user_id: "dev-costcenter-001",
    display_name: "Cost Center Owner",
    email: "owner@obs.local",
    is_active: true,
    is_administrator: false,
    is_system_modeler: false,
    is_report_developer: false,
    is_finance_reviewer: false,
  },
  {
    user_id: "dev-finance-001",
    display_name: "Finance Reviewer",
    email: "finance@obs.local",
    is_active: true,
    is_administrator: false,
    is_system_modeler: false,
    is_report_developer: false,
    is_finance_reviewer: true,
  },
];

const grants: Record<string, CostCenterGrant[]> = {
  "dev-costcenter-001": [
    {
      user_id: "dev-costcenter-001",
      cost_center_id: "CC-1001",
      standard_grant: "ALL_WRITE",
      confidential_grant: "ALL_WRITE",
      granted_by: "dev-admin-001",
      granted_at: "2026-01-01T00:00:00Z",
    },
  ],
};

function findUser(userId: string): User | undefined {
  return users.find((u) => u.user_id === userId);
}

export const handlers = [
  http.post("/api/v1/auth/refresh", () => HttpResponse.json({ access_token: "mock", expires_in: 3600 })),
  http.post("/api/v1/auth/logout", () => HttpResponse.json({ logout_url: "/" })),

  http.get("/api/v1/me", () => HttpResponse.json(me)),

  http.get("/api/v1/users", () => HttpResponse.json(users)),

  http.post("/api/v1/users", async ({ request }) => {
    const body = (await request.json()) as Partial<User>;
    const userId = body.user_id?.trim() ?? "";
    // Mirror the backend's 422 validation (Security Spec §9.2) for missing fields.
    if (!userId || !body.display_name?.trim() || !body.email?.trim()) {
      return HttpResponse.json({ detail: "Validation error." }, { status: 422 });
    }
    if (findUser(userId)) {
      return HttpResponse.json({ detail: "A user with this user_id already exists." }, { status: 409 });
    }
    const created: User = {
      user_id: userId,
      display_name: body.display_name,
      email: body.email,
      is_active: body.is_active ?? false,
      is_administrator: body.is_administrator ?? false,
      is_system_modeler: body.is_system_modeler ?? false,
      is_report_developer: body.is_report_developer ?? false,
      is_finance_reviewer: body.is_finance_reviewer ?? false,
    };
    users.push(created);
    return HttpResponse.json(created, { status: 201 });
  }),

  http.patch("/api/v1/users/:userId", async ({ params, request }) => {
    const user = findUser(params.userId as string);
    if (!user) return HttpResponse.json({ detail: "User not found." }, { status: 404 });
    const changes = (await request.json()) as Partial<User>;
    // Mirror the last-active-Administrator guard (AC-CAP-08): block removing the
    // final active Administrator's admin rights or login access.
    const activeAdmins = users.filter((u) => u.is_active && u.is_administrator);
    const isLastActiveAdmin = user.is_active && user.is_administrator && activeAdmins.length <= 1;
    const removesAdmin = changes.is_administrator === false || changes.is_active === false;
    if (isLastActiveAdmin && removesAdmin) {
      return HttpResponse.json(
        { detail: "Cannot deactivate or remove Administrator from the last active Administrator." },
        { status: 409 },
      );
    }
    Object.assign(user, changes);
    return HttpResponse.json(user);
  }),

  http.get("/api/v1/users/:userId/grants", ({ params }) =>
    HttpResponse.json(grants[params.userId as string] ?? []),
  ),

  http.post("/api/v1/users/:userId/grants", async ({ params, request }) => {
    const userId = params.userId as string;
    const body = (await request.json()) as Omit<CostCenterGrant, "user_id" | "granted_by" | "granted_at">;
    const grant: CostCenterGrant = {
      user_id: userId,
      cost_center_id: body.cost_center_id,
      standard_grant: body.standard_grant,
      confidential_grant: body.confidential_grant ?? "ALL_NONE",
      granted_by: "dev-admin-001",
      granted_at: new Date().toISOString(),
    };
    const existing = grants[userId] ?? [];
    grants[userId] = [...existing.filter((g) => g.cost_center_id !== grant.cost_center_id), grant];
    return HttpResponse.json(grant, { status: 201 });
  }),

  http.delete("/api/v1/users/:userId/grants/:costCenterId", ({ params }) => {
    const userId = params.userId as string;
    const costCenterId = params.costCenterId as string;
    const existing = grants[userId] ?? [];
    if (!existing.some((g) => g.cost_center_id === costCenterId)) {
      return HttpResponse.json({ detail: "Grant not found." }, { status: 404 });
    }
    grants[userId] = existing.filter((g) => g.cost_center_id !== costCenterId);
    return new HttpResponse(null, { status: 204 });
  }),

  http.post("/api/v1/users/:userId/rollup-grants", async ({ params, request }) => {
    const body = (await request.json()) as { rollup_id: string };
    return HttpResponse.json({ user_id: params.userId as string, rollup_id: body.rollup_id }, { status: 201 });
  }),
];
