import type { Money } from "./money";

export interface Run {
  id: string;
  status: string;
  window_start: string;
  window_end: string;
  started_at: string;
  finished_at: string | null;
  order_count: number;
  finding_count: number;
  exception_count: number;
}

export interface Decision {
  id: string;
  outcome: "approved" | "rejected";
  approver: string;
  note: string | null;
  decided_at: string;
  investigation_id: string | null;
}

export interface InvestigationSummary {
  id: string;
  verdict: "reported" | "insufficient_evidence" | "failed";
  provider: string;
  model_requested: string;
  started_at: string;
  top_confidence: string | null;
}

export interface ExceptionRow {
  id: string;
  run_id: string;
  code: string;
  order_ref: string;
  detail: string;
  certainty: "confirmed" | "suspected";
  impact: Money;
  expected: Money | null;
  reported: Money | null;
  seller_id: string | null;
  seller_name: string | null;
  seller_count: number;
  order_placed_at: string | null;
  evidence_refs: string[];
  evidence_basis: string;
  priority: { score: string; factors: Record<string, string | null> };
  investigation: InvestigationSummary | null;
  decision: Decision | null;
}

export interface Cause {
  rank: number;
  description: string;
  confidence: string;
  cited_refs: string[];
  unresolved_refs: string[];
}

export interface InvestigationDetail extends InvestigationSummary {
  downgraded: boolean;
  stop_reason: string | null;
  model_returned: string | null;
  summarized_reasoning: string | null;
  withheld: string[];
  finished_at: string | null;
  causes: Cause[];
}

export interface Job {
  id: string;
  status: "queued" | "claimed" | "done" | "failed";
  attempts: number;
  last_error: string | null;
  created_at: string;
}

export interface ExceptionDetail extends ExceptionRow {
  investigation_detail: InvestigationDetail | null;
  decisions: Decision[];
  investigation_job: Job | null;
}

// FastAPI puts the reason in `detail`: a string for an HTTPException, a list of
// field errors for a 422. Showing it beats a bare status line.
async function describe(response: Response, path: string): Promise<string> {
  let reason = response.statusText;
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") {
      reason = body.detail;
    } else if (Array.isArray(body.detail)) {
      reason = body.detail
        .map((d: { loc?: unknown[]; msg?: string }) =>
          `${(d.loc ?? []).slice(1).join(".")}: ${d.msg ?? "invalid"}`,
        )
        .join("; ");
    }
  } catch {
    /* a non-JSON error body keeps the status text */
  }
  return `${response.status} ${reason} (${path})`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    throw new Error(await describe(response, path));
  }
  return (await response.json()) as T;
}

export interface EvidenceField {
  label: string;
  value: string | null;
  money: Money | null;
}

export interface Evidence {
  ref: string;
  kind: string;
  stable: boolean;
  fields: EvidenceField[];
  raw: {
    source: string;
    record_type: string;
    retrieved_at: string;
    endpoint: string | null;
    sha256: string;
  } | null;
}

export interface Coverage {
  orders_total: number;
  with_exceptions: number;
  parked: number;
  clean: number;
  not_examined: number;
  not_examined_orders: string[];
  parked_items: { id: string; order_ref: string; detail: string; run_id: string }[];
  clean_orders: {
    order_ref: string;
    total: Money;
    placed_at: string;
    sellers: string[];
  }[];
  basis: string;
}

export interface RunRequest {
  status: "queued" | "in_flight" | "already_done" | "no_orders";
  window_start: string | null;
  window_end: string | null;
  job: Job | null;
  message: string;
}

export interface GridAssistantResult {
  gridState: Record<string, unknown>;
  propertiesToIgnore: string[];
  explanation: string;
}

export const api = {
  evidence: (ref: string) =>
    request<Evidence>(`/api/evidence/${encodeURIComponent(ref)}`),
  evidenceRaw: (ref: string) =>
    request<Record<string, unknown>>(
      `/api/evidence/${encodeURIComponent(ref)}/raw`,
    ),
  coverage: () => request<Coverage>("/api/coverage"),
  requestRun: () => request<RunRequest>("/api/runs", { method: "POST" }),
  job: (id: string) => request<Job>(`/api/jobs/${id}`),
  gridAssistant: (body: {
    request: string;
    state: Record<string, unknown>;
    schema: Record<string, unknown>;
  }) =>
    request<GridAssistantResult>("/api/grid-assistant", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  runs: () => request<Run[]>("/api/runs"),
  exceptions: (runId?: string) =>
    request<ExceptionRow[]>(
      runId ? `/api/exceptions?run_id=${runId}` : "/api/exceptions",
    ),
  exception: (id: string) => request<ExceptionDetail>(`/api/exceptions/${id}`),
  investigate: (id: string) =>
    request<Job>(`/api/exceptions/${id}/investigate`, { method: "POST" }),
  decide: (
    id: string,
    body: { outcome: "approved" | "rejected"; approver: string; note?: string },
  ) =>
    request<ExceptionDetail>(`/api/exceptions/${id}/decision`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
};
