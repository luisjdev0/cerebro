import { useState } from 'react';
import { ChartLine, ChartPie } from 'lucide-react';
import { CartesianGrid, Cell, Line, LineChart, Pie, PieChart, XAxis, YAxis } from 'recharts';
import { Button, ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from '@alquimias/ixchel';
import { useTranslation } from 'react-i18next';
import { useLocale } from '../lib/i18n';
import type { MonthPoint, Slice } from '../lib/charts';

const COLORS = ['var(--chart-1)', 'var(--chart-2)', 'var(--chart-3)', 'var(--chart-4)', 'var(--chart-5)'];
const OTHER = 'var(--muted-foreground)';

type Props = {
  title: string;
  /** Label of the second line series: edits for memories/docs, runs for flows. */
  updatedLabel: string;
  series: MonthPoint[];
  slices: Slice[];
  /** Wording of the pie ("size of each context", ...). */
  pieCaption: string;
  empty?: boolean;
};

/** One dashboard card with a line (evolution per month) / pie (share of each group) switch. */
export function ModuleChart({ title, updatedLabel, series, slices, pieCaption, empty }: Props) {
  const { t } = useTranslation();
  const { lang } = useLocale();
  const [mode, setMode] = useState<'line' | 'pie'>('line');
  const monthFmt = new Intl.DateTimeFormat(lang, { month: 'short', timeZone: 'UTC' });
  const monthLabel = (m: string) => monthFmt.format(new Date(`${m}-01T00:00:00Z`));

  const lineConfig: ChartConfig = {
    created: { label: t('charts.created'), color: 'var(--chart-1)' },
    updated: { label: updatedLabel, color: 'var(--chart-3)' },
  };
  const pieConfig: ChartConfig = Object.fromEntries(
    slices.map((s, i) => [s.key, { label: s.key === '__other' ? t('charts.other') : s.name, color: s.key === '__other' ? OTHER : COLORS[i % COLORS.length] }]),
  );
  const total = slices.reduce((a, s) => a + s.value, 0);

  return (
    <section className="space-y-3 rounded-2xl border p-4">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">{title}</h2>
        <div role="group" aria-label={t('charts.type')} className="flex gap-1">
          <Button size="sm" variant={mode === 'line' ? 'primary' : 'ghost'} aria-pressed={mode === 'line'} aria-label={t('charts.line')} title={t('charts.line')} onClick={() => setMode('line')}>
            <ChartLine className="size-4" />
          </Button>
          <Button size="sm" variant={mode === 'pie' ? 'primary' : 'ghost'} aria-pressed={mode === 'pie'} aria-label={t('charts.pie')} title={t('charts.pie')} onClick={() => setMode('pie')}>
            <ChartPie className="size-4" />
          </Button>
        </div>
      </div>

      {empty ? (
        <p className="flex h-48 items-center justify-center text-sm text-muted-foreground">{t('charts.noData')}</p>
      ) : mode === 'line' ? (
        <ChartContainer config={lineConfig} className="aspect-auto h-52 w-full">
          <LineChart data={series} margin={{ left: 0, right: 8, top: 8 }}>
            <CartesianGrid vertical={false} />
            <XAxis dataKey="month" tickLine={false} axisLine={false} tickFormatter={monthLabel} interval="preserveStartEnd" />
            <YAxis allowDecimals={false} width={28} tickLine={false} axisLine={false} />
            <ChartTooltip content={<ChartTooltipContent labelFormatter={(m) => monthLabel(String(m))} />} />
            <Line dataKey="created" type="monotone" stroke="var(--color-created)" strokeWidth={2} dot={false} />
            <Line dataKey="updated" type="monotone" stroke="var(--color-updated)" strokeWidth={2} dot={false} />
          </LineChart>
        </ChartContainer>
      ) : slices.length === 0 ? (
        <p className="flex h-48 items-center justify-center text-sm text-muted-foreground">{t('charts.noData')}</p>
      ) : (
        <div className="space-y-2">
          <ChartContainer config={pieConfig} className="aspect-auto h-44 w-full">
            <PieChart>
              <ChartTooltip content={<ChartTooltipContent nameKey="key" hideLabel />} />
              <Pie data={slices} dataKey="value" nameKey="key" innerRadius={38} outerRadius={70} paddingAngle={2}>
                {slices.map((s, i) => (
                  <Cell key={s.key} fill={s.key === '__other' ? OTHER : COLORS[i % COLORS.length]} />
                ))}
              </Pie>
            </PieChart>
          </ChartContainer>
          <p className="text-center text-[11px] text-muted-foreground">{pieCaption}</p>
          <ul className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
            {slices.map((s, i) => (
              <li key={s.key} className="flex items-center gap-1.5 truncate">
                <span className="size-2.5 shrink-0 rounded-sm" style={{ background: s.key === '__other' ? OTHER : COLORS[i % COLORS.length] }} />
                <span className="truncate">{s.key === '__other' ? t('charts.other') : s.name}</span>
                <span className="ml-auto text-muted-foreground">{Math.round((s.value / total) * 100)}%</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
