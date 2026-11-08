import { useMemo } from 'react';
import { buildGrid, levelFor } from '../lib/activity';

const LEVEL_CLASS = [
  'bg-muted',
  'bg-brand-soft/25',
  'bg-brand-soft/50',
  'bg-brand-soft/75',
  'bg-brand-soft',
];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** GitHub-style contribution grid: one square per day, darker = more activity. */
export function Heatmap({ totals, weeks = 53 }: { totals: Map<string, number>; weeks?: number }) {
  const grid = useMemo(() => buildGrid(new Date(), weeks), [weeks]);
  const max = useMemo(() => Math.max(0, ...totals.values()), [totals]);
  const total = useMemo(() => [...totals.values()].reduce((a, b) => a + b, 0), [totals]);

  // Label a month above the first week that starts in it.
  const monthLabels = grid.map((col, i) => {
    const m = new Date(col[0].date + 'T00:00:00Z').getUTCMonth();
    const prev = i > 0 ? new Date(grid[i - 1][0].date + 'T00:00:00Z').getUTCMonth() : -1;
    return m !== prev ? MONTHS[m] : '';
  });

  return (
    <div className="space-y-2">
      <div className="overflow-x-auto pb-1">
        <div className="inline-flex flex-col gap-1">
          <div className="flex gap-[3px] pl-0 text-[10px] text-muted-foreground">
            {monthLabels.map((label, i) => (
              <div key={i} className="w-3 overflow-visible whitespace-nowrap">{label}</div>
            ))}
          </div>
          <div className="flex gap-[3px]" role="img" aria-label={`${total} events in the last year`}>
            {grid.map((col, ci) => (
              <div key={ci} className="flex flex-col gap-[3px]">
                {col.map((d) => {
                  if (!d.inRange) return <div key={d.date} className="size-3" />;
                  const count = totals.get(d.date) ?? 0;
                  return (
                    <div
                      key={d.date}
                      title={`${count} ${count === 1 ? 'event' : 'events'} on ${d.date}`}
                      className={`size-3 rounded-[3px] ${LEVEL_CLASS[levelFor(count, max)]}`}
                    />
                  );
                })}
              </div>
            ))}
          </div>
        </div>
      </div>
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>{total.toLocaleString()} events in the last year</span>
        <span className="flex items-center gap-1">
          Less
          {LEVEL_CLASS.map((c) => (
            <span key={c} className={`size-3 rounded-[3px] ${c}`} />
          ))}
          More
        </span>
      </div>
    </div>
  );
}
