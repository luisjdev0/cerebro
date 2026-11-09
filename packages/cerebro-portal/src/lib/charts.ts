import type { ActivityDay } from './activity';

export type MonthPoint = { month: string; created: number; updated: number };
export type Slice = { key: string; name: string; value: number };

/** Buckets per-day activity into the last `months` calendar months (UTC), oldest first,
 *  with zeros for empty months so the line has no gaps. `month` is 'YYYY-MM'. */
export function monthlySeries(days: ActivityDay[], now = new Date(), months = 12): MonthPoint[] {
  const out: MonthPoint[] = [];
  for (let i = months - 1; i >= 0; i--) {
    const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - i, 1));
    out.push({ month: d.toISOString().slice(0, 7), created: 0, updated: 0 });
  }
  const byMonth = new Map(out.map((p) => [p.month, p]));
  for (const day of days) {
    const p = byMonth.get(day.date.slice(0, 7));
    if (p) {
      p.created += day.created;
      p.updated += day.updated;
    }
  }
  return out;
}

/** January to December of `year` (UTC), zero-filled; for the current year it stops at the
 *  current month so the line does not fall to zero over months that have not happened. */
export function yearSeries(days: ActivityDay[], year: number, now = new Date()): MonthPoint[] {
  const last = year === now.getUTCFullYear() ? now.getUTCMonth() : year > now.getUTCFullYear() ? -1 : 11;
  const out: MonthPoint[] = [];
  for (let m = 0; m <= last; m++) out.push({ month: `${year}-${String(m + 1).padStart(2, '0')}`, created: 0, updated: 0 });
  const byMonth = new Map(out.map((p) => [p.month, p]));
  for (const day of days) {
    const p = byMonth.get(day.date.slice(0, 7));
    if (p) {
      p.created += day.created;
      p.updated += day.updated;
    }
  }
  return out;
}

/** Keeps the `max` biggest slices (zero-sized ones dropped) and folds the rest into one
 *  `otherKey` slice, so a pie never needs more colors than the theme defines. */
export function topSlices(items: { key: string; name: string; value: number }[], max = 5, other = { key: '__other', name: 'Other' }): Slice[] {
  const sorted = items.filter((i) => i.value > 0).sort((a, b) => b.value - a.value);
  if (sorted.length <= max) return sorted;
  const head = sorted.slice(0, max - 1);
  const rest = sorted.slice(max - 1).reduce((a, i) => a + i.value, 0);
  return [...head, { key: other.key, name: other.name, value: rest }];
}
