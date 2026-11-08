import { api } from './api';

export type FlowSortKey = 'updated_at' | 'created_at' | 'code' | 'name' | 'category';
export type RunSortKey = 'started_at' | 'completed_at' | 'status' | 'flow_code';
export type RunStatus = 'in_progress' | 'completed' | 'aborted';

export type FlowSummary = {
  id: string;
  code: string;
  category: string;
  name: string;
  description: string | null;
  status: string;
  current_version: number;
  created_by: string | null;
  created_at: string;
  updated_at: string;
};
export type Flow = FlowSummary & { yaml_content: string };
export type FlowVersion = { version_number: number; yaml_content: string; created_at: string };
export type FlowCategory = { id: string; slug: string; code: string; name: string; description: string | null };

export type RunSummary = {
  run_id: string;
  flow_code: string;
  flow_name: string;
  category: string;
  definition_version: number;
  status: RunStatus | string;
  started_at: string;
  completed_at: string | null;
  current_step_id?: string | null;
  awaiting_approval?: boolean;
  expired?: boolean;
};
export type RunEvent = { event_type: string; step_id: string | null; payload: unknown; created_at: string };
/** The current step as the engine describes it (Step model, plus the synthetic prerequisites step). */
export type RunStep = {
  id: string;
  type: string;
  description?: string;
  next?: string;
  branches?: Record<string, string>;
  checkpoint?: { required: boolean; prompt: string; on_reject?: string };
  tools_required?: string[];
};
export type RunState = { run_id: string; status: string; step: RunStep | RunStep[] | null };

export type FlowListParams = { category?: string; q?: string; sort: FlowSortKey; order: 'asc' | 'desc'; limit: number; offset: number };
export type RunListParams = {
  status: RunStatus | 'all';
  awaiting?: boolean;
  category?: string;
  sort: RunSortKey;
  order: 'asc' | 'desc';
  limit: number;
  offset: number;
};

function qs(params: Record<string, string | number | boolean | undefined>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== '' && v !== false) u.set(k, String(v));
  return u.toString();
}

export const browseFlows = (p: FlowListParams) => api<{ items: FlowSummary[]; total: number }>(`/flows/flows/browse?${qs({ ...p })}`);
export const getFlow = (code: string) => api<Flow>(`/flows/flows/${encodeURIComponent(code)}`);
export const getFlowVersions = (code: string) => api<FlowVersion[]>(`/flows/flows/${encodeURIComponent(code)}/versions`);
export const listFlowCategories = () => api<FlowCategory[]>('/flows/categories');
export const createFlowCategory = (c: { slug: string; code: string; name: string; description?: string }) =>
  api<FlowCategory>('/flows/categories', { method: 'POST', json: c });
/** Only name and description change: the slug (token scopes) and the code (flow ids) are permanent. */
export const updateFlowCategory = (slug: string, c: { name?: string; description?: string }) =>
  api<FlowCategory>(`/flows/categories/${encodeURIComponent(slug)}`, { method: 'PATCH', json: c });
export const deleteFlowCategory = (slug: string, force: boolean) =>
  api<{ slug: string; status: string; flows_deleted: number }>(`/flows/categories/${encodeURIComponent(slug)}?force=${force}`, { method: 'DELETE' });
export const countCategoryFlows = (slug: string) =>
  browseFlows({ category: slug, sort: 'updated_at', order: 'desc', limit: 1, offset: 0 }).then((r) => r.total);
export const validateFlow = (yaml_content: string) => api<{ valid: boolean }>('/flows/flows/validate', { method: 'POST', json: { yaml_content } });
export const createFlow = (category: string, yaml_content: string) => api<Flow>('/flows/flows', { method: 'POST', json: { category, yaml_content } });
export const updateFlow = (code: string, yaml_content: string) => api<Flow>(`/flows/flows/${encodeURIComponent(code)}`, { method: 'PATCH', json: { yaml_content } });
export const deleteFlow = (code: string) => api<unknown>(`/flows/flows/${encodeURIComponent(code)}`, { method: 'DELETE' });
export const startFlow = (code: string) => api<RunState>(`/flows/flows/${encodeURIComponent(code)}/start`, { method: 'POST' });

export const browseRuns = (p: RunListParams) => api<{ items: RunSummary[]; total: number }>(`/flows/runs?${qs({ ...p })}`);
export const getRun = (id: string) => api<RunState>(`/flows/runs/${id}`);
export const getRunEvents = (id: string) => api<RunEvent[]>(`/flows/runs/${id}/events`);

export type FlowReference = { ref: string; kind: 'metadata' | 'prompt'; step_id: string | null };
export const getFlowDiagram = (code: string, version?: number) =>
  api<{ code: string; version: number; format: string; diagram: string }>(
    `/flows/flows/${encodeURIComponent(code)}/diagram?format=mermaid${version ? `&version=${version}` : ''}`,
  );
export const getFlowReferences = (code: string) => api<FlowReference[]>(`/flows/flows/${encodeURIComponent(code)}/references`);

/** `cerebro-docs://category/slug` -> its parts; anything else is not a document link. */
export function parseDocRef(ref: string): { category: string; slug: string } | null {
  const m = /^cerebro-docs:\/\/([^/\s]+)\/(\S+)$/.exec(ref.trim());
  return m ? { category: m[1], slug: m[2] } : null;
}
