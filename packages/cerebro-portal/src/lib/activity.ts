import { api } from './api';

export type ActivityDay = { date: string; created: number; updated: number };
export type ModuleKey = 'memory' | 'docs' | 'flows';
export type ActivityByModule = Record<ModuleKey, ActivityDay[]>;

const PATHS: Record<ModuleKey, string> = {
  memory: '/memory/activity',
  docs: '/docs/activity',
  flows: '/flows/activity',
};

export type ActivityResult = { byModule: ActivityByModule; years: number[] };

/** Fetches the per-day activity of every module the token can reach: the rolling last
 *  `days` days, or the calendar `year` when given. A module that rejects the request
 *  (scope, outage) just contributes no days instead of failing the page. `years` is the
 *  union of the years that have any activity, newest first. */
export async function fetchActivity(modules: ModuleKey[], opts: { days?: number; year?: number | null } = {}): Promise<ActivityResult> {
  const query = opts.year ? `year=${opts.year}` : `days=${opts.days ?? 365}`;
  const byModule: ActivityByModule = { memory: [], docs: [], flows: [] };
  const years = new Set<number>();
  await Promise.all(
    modules.map(async (m) => {
      try {
        const res = await api<{ days: ActivityDay[]; years?: number[] }>(`${PATHS[m]}?${query}`);
        byModule[m] = res.days;
        (res.years ?? []).forEach((y) => years.add(y));
      } catch {
        byModule[m] = [];
      }
    }),
  );
  return { byModule, years: [...years].sort((x, y) => y - x) };
}

/** date (YYYY-MM-DD) -> total events across the selected modules. */
export function totalsByDate(activity: ActivityByModule, modules: ModuleKey[]): Map<string, number> {
  const map = new Map<string, number>();
  for (const m of modules) {
    for (const d of activity[m]) map.set(d.date, (map.get(d.date) ?? 0) + d.created + d.updated);
  }
  return map;
}

/** Maps a count to one of 5 intensity levels (0 = no activity), relative to the busiest day. */
export function levelFor(count: number, max: number): 0 | 1 | 2 | 3 | 4 {
  if (count <= 0 || max <= 0) return 0;
  const r = count / max;
  if (r > 0.75) return 4;
  if (r > 0.5) return 3;
  if (r > 0.25) return 2;
  return 1;
}

/** Builds the GitHub-style grid: columns are weeks (Sunday first), rows are weekdays.
 *  Covers the `weeks` most recent weeks ending today (UTC dates, like the API). */
export function buildGrid(today: Date, weeks = 53): { date: string; inRange: boolean }[][] {
  const end = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate()));
  const start = new Date(end);
  start.setUTCDate(start.getUTCDate() - end.getUTCDay() - 7 * (weeks - 1)); // Sunday of the first week
  const cols: { date: string; inRange: boolean }[][] = [];
  const cursor = new Date(start);
  for (let w = 0; w < weeks; w++) {
    const col: { date: string; inRange: boolean }[] = [];
    for (let i = 0; i < 7; i++) {
      col.push({ date: cursor.toISOString().slice(0, 10), inRange: cursor <= end });
      cursor.setUTCDate(cursor.getUTCDate() + 1);
    }
    cols.push(col);
  }
  return cols;
}

/** Grid for one calendar year: columns are weeks (Sunday first) from the week holding
 *  Jan 1 to the week holding Dec 31. Days outside the year, or after `today`, are not in range. */
export function buildYearGrid(year: number, today: Date): { date: string; inRange: boolean }[][] {
  const first = new Date(Date.UTC(year, 0, 1));
  const last = new Date(Date.UTC(year, 11, 31));
  const todayUtc = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate()));
  const cursor = new Date(first);
  cursor.setUTCDate(cursor.getUTCDate() - first.getUTCDay());
  const cols: { date: string; inRange: boolean }[][] = [];
  while (cursor <= last) {
    const col: { date: string; inRange: boolean }[] = [];
    for (let i = 0; i < 7; i++) {
      col.push({ date: cursor.toISOString().slice(0, 10), inRange: cursor >= first && cursor <= last && cursor <= todayUtc });
      cursor.setUTCDate(cursor.getUTCDate() + 1);
    }
    cols.push(col);
  }
  return cols;
}
