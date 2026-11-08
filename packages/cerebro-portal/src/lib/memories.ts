import { api } from './api';

export type MemoryType = 'semantic' | 'episodic' | 'procedural' | 'decision';
export type MemoryStatus = 'active' | 'superseded' | 'archived';
export const MEMORY_TYPES: MemoryType[] = ['semantic', 'episodic', 'procedural', 'decision'];
export const MEMORY_STATUSES: MemoryStatus[] = ['active', 'superseded', 'archived'];

export type Memory = {
  id: string;
  context: string;
  type: MemoryType | string;
  title: string;
  content: string;
  importance: number;
  confidence: number;
  source: string | null;
  status: MemoryStatus | string;
  superseded_by: string | null;
  occurred_at: string | null;
  created_at: string;
  updated_at: string;
  score?: number;
};
export type Context = { slug: string; name: string; kind: string; description: string | null };
export type Related = { relation: string; direction: 'outgoing' | 'incoming' | string; note: string | null; virtual: boolean; memory: Memory };

export type SortKey = 'created_at' | 'updated_at' | 'importance' | 'title' | 'type' | 'context';
export type ListParams = {
  context?: string;
  type?: string;
  status?: MemoryStatus | 'all';
  sort: SortKey;
  order: 'asc' | 'desc';
  limit: number;
  offset: number;
};

function qs(params: Record<string, string | number | undefined>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== '') u.set(k, String(v));
  return u.toString();
}

export const listMemories = (p: ListParams) =>
  api<{ items: Memory[]; total: number }>(`/memory/memories?${qs({ ...p })}`);

/** Semantic search. Without a context it searches everywhere (`scope=all`), so the
 *  Context Engine never answers with a disambiguation prompt the table cannot show. */
export const searchMemories = (q: string, opts: { context?: string; type?: string; limit?: number }) =>
  api<{ results: Memory[] }>(
    `/memory/memories/search?${qs({ q, context: opts.context, type: opts.type, limit: opts.limit ?? 50, scope: opts.context ? undefined : 'all' })}`,
  ).then((r) => r.results);

export const getMemory = (id: string) => api<Memory>(`/memory/memories/${id}`);
export const getRelated = (id: string) => api<{ related: Related[] }>(`/memory/memories/${id}/related`).then((r) => r.related);
export const listContexts = () => api<Context[]>('/memory/contexts');

export const CONTEXT_KINDS = ['project', 'domain', 'person', 'org'] as const;

export type NewMemory = { content: string; context: string; type: MemoryType; title?: string; importance?: number; occurred_at?: string };
export const createMemory = (m: NewMemory) => api<Memory>('/memory/memories', { method: 'POST', json: m });
/** Editing never rewrites in place: the API stores a new version and marks the old one superseded. */
export type MemoryPatch = { content?: string; title?: string; type?: MemoryType; importance?: number; occurred_at?: string | null };
export const updateMemory = (id: string, patch: MemoryPatch) => api<Memory>(`/memory/memories/${id}`, { method: 'PATCH', json: patch });
export const forgetMemory = (id: string) => api<unknown>(`/memory/memories/${id}`, { method: 'DELETE' });
export const createContext = (c: { slug: string; name: string; kind: string; description?: string }) =>
  api<Context>('/memory/contexts', { method: 'POST', json: c });

export const updateContext = (slug: string, patch: { name?: string; kind?: string; description?: string }) =>
  api<Context>(`/memory/contexts/${slug}`, { method: 'PATCH', json: patch });
export const deleteContext = (slug: string, force: boolean) =>
  api<{ slug: string; status: string; memories_deleted: number }>(`/memory/contexts/${slug}?force=${force}`, { method: 'DELETE' });
export const countContextMemories = (slug: string) =>
  listMemories({ context: slug, status: 'all', sort: 'created_at', order: 'desc', limit: 1, offset: 0 }).then((r) => r.total);

export const slugify = (s: string) =>
  s.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 100);
