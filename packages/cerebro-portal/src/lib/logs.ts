import { api } from './api';

export type LogLine = { key: string; at: string; text: string };
export type LogPage = { lines: LogLine[]; next: string | null };

type MemoryEntry = { id: number; created_at: string; agent: string; action: string; memory_id: string | null; detail: Record<string, unknown> | null };
type FlowEntry = { seq: number; created_at: string; run_id: string; flow_code: string; event_type: string; step_id: string | null; payload: Record<string, unknown> | null };

const json = (v: unknown) => (v == null ? '' : ` ${JSON.stringify(v)}`);

/**
 * One entry per module that records logs today. Adding coverage later (docs, auth...) is adding an
 * entry here and the endpoint behind it: the screen draws a tab per source.
 */
export type LogSource = {
  id: string;
  /** Module whose access gates the tab (a token without it could not read the endpoint anyway). */
  module: 'memory' | 'docs' | 'flows';
  fetch: (before?: string) => Promise<LogPage>;
};

const qs = (before?: string) => `?limit=200${before ? `&before=${encodeURIComponent(before)}` : ''}`;

export const LOG_SOURCES: LogSource[] = [
  {
    id: 'memory',
    module: 'memory',
    fetch: async (before) => {
      const r = await api<{ items: MemoryEntry[]; next: string | null }>(`/memory/logs${qs(before)}`);
      return {
        next: r.next,
        lines: r.items.map((e) => ({
          key: String(e.id),
          at: e.created_at,
          text: `agent=${e.agent} action=${e.action}${e.memory_id ? ` memory=${e.memory_id}` : ''}${json(e.detail)}`,
        })),
      };
    },
  },
  {
    id: 'flows',
    module: 'flows',
    fetch: async (before) => {
      const r = await api<{ items: FlowEntry[]; next: string | null }>(`/flows/logs${qs(before)}`);
      return {
        next: r.next,
        lines: r.items.map((e) => ({
          key: String(e.seq),
          at: e.created_at,
          text: `flow=${e.flow_code} run=${e.run_id} event=${e.event_type}${e.step_id ? ` step=${e.step_id}` : ''}${json(e.payload)}`,
        })),
      };
    },
  },
];

export const lineToText = (l: LogLine) => `${l.at} ${l.text}`;
