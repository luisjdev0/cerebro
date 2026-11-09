import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { levelFor } from '../lib/activity';
import { useLocale } from '../lib/i18n';

const CELL = 15; // max square size in px
const LEVEL_CLASS = ['bg-muted', 'bg-brand-soft/25', 'bg-brand-soft/50', 'bg-brand-soft/75', 'bg-brand-soft'];

type Props = {
  grid: { date: string; inRange: boolean }[][];
  totals: Map<string, number>;
  /** Selected calendar year, or null for the rolling last year (only changes the summary text). */
  year: number | null;
};

/** GitHub-style contribution grid: one square per day, darker = more activity. The
 *  columns share the available width, so the grid fills the card at any screen size. */
export function Heatmap({ grid, totals, year }: Props) {
  const { t } = useTranslation();
  const { months, formatDate, lang } = useLocale();
  const max = useMemo(() => Math.max(0, ...totals.values()), [totals]);
  const total = useMemo(() => grid.flat().reduce((a, d) => a + (d.inRange ? (totals.get(d.date) ?? 0) : 0), 0), [grid, totals]);
  const weekday = (dow: number) =>
    new Intl.DateTimeFormat(lang, { weekday: 'short', timeZone: 'UTC' }).format(new Date(Date.UTC(2024, 0, 7 + dow)));

  // Label a month above the first week that starts in it.
  const monthLabels = grid.map((col, i) => {
    const m = new Date(col[0].date + 'T00:00:00Z').getUTCMonth();
    const prev = i > 0 ? new Date(grid[i - 1][0].date + 'T00:00:00Z').getUTCMonth() : -1;
    return m !== prev ? months[m] : '';
  });
  const summary = year ? t('heatmap.summaryYear', { total: total.toLocaleString(lang), year }) : t('heatmap.summary', { total: total.toLocaleString(lang) });

  return (
    <div className="space-y-2" style={{ maxWidth: 34 + grid.length * (CELL + 3) }}>
      <div
        className="grid gap-[3px]"
        // Columns share the card width but the squares stop growing at CELL px, so the
        // grid stays compact (and centered) on wide screens instead of getting taller.
        style={{ gridTemplateColumns: `auto repeat(${grid.length}, minmax(0, 1fr))` }}
        role="img"
        aria-label={summary}
      >
        {monthLabels.map((label, i) => (
          <div key={`m${i}`} className="h-4 overflow-visible text-[10px] leading-4 whitespace-nowrap text-muted-foreground" style={{ gridRow: 1, gridColumn: i + 2 }}>
            {label}
          </div>
        ))}
        {[1, 3, 5].map((dow) => (
          <div key={`d${dow}`} className="flex items-center pr-2 text-[10px] text-muted-foreground" style={{ gridRow: dow + 2, gridColumn: 1 }}>
            {weekday(dow)}
          </div>
        ))}
        {grid.map((col, ci) =>
          col.map((d, ri) => {
            const style = { gridRow: ri + 2, gridColumn: ci + 2 };
            if (!d.inRange) return <div key={d.date} style={style} />;
            const count = totals.get(d.date) ?? 0;
            return (
              <div
                key={d.date}
                style={style}
                title={t('heatmap.cell', { count, date: formatDate(d.date) })}
                className={`aspect-square rounded-[3px] ${LEVEL_CLASS[levelFor(count, max)]}`}
              />
            );
          }),
        )}
      </div>
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>{summary}</span>
        <span className="flex items-center gap-1">
          {t('heatmap.less')}
          {LEVEL_CLASS.map((c) => (
            <span key={c} className={`size-3 rounded-[3px] ${c}`} />
          ))}
          {t('heatmap.more')}
        </span>
      </div>
    </div>
  );
}
