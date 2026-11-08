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

export type SortKey = 'created_at' | 'updated_at' | 'importance';
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
