import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Brain, FileText, Workflow } from 'lucide-react';
import { Button, PageHeader, StatCard } from '@alquimias/ixchel';
import { useTranslation } from 'react-i18next';
import { Heatmap } from '../components/Heatmap';
import { SortableBlocks } from '../components/SortableBlocks';
import { ModuleChart } from '../components/ModuleChart';
import { monthlySeries, topSlices, type Slice } from '../lib/charts';
import { api } from '../lib/api';
import { useAuth } from '../lib/auth';
import { buildGrid, buildYearGrid, fetchActivity, totalsByDate, type ActivityByModule, type ModuleKey } from '../lib/activity';

type Stats = {
  memories: number | null;
  contexts: number | null;
  documents: number | null;
  categories: number | null;
  flows: number | null;
  runs: number | null;
};
type Breakdown = { memory: Slice[]; docs: Slice[]; flows: Slice[] };
type CategoryCount = { slug: string; name: string; count: number; code?: string };

const EMPTY: Stats = { memories: null, contexts: null, documents: null, categories: null, flows: null, runs: null };
const FILTERS: { key: 'all' | ModuleKey; label: string }[] = [
  { key: 'all', label: 'dashboard.filterAll' },
  { key: 'memory', label: 'nav.memory' },
  { key: 'docs', label: 'nav.documents' },
  { key: 'flows', label: 'nav.workflows' },
];

async function tryGet<T>(path: string): Promise<T | null> {
  try {
    return await api<T>(path);
  } catch {
    return null;
  }
}

function CardLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link to={to} className="block rounded-2xl transition-transform hover:-translate-y-0.5 focus-visible:outline-2 focus-visible:outline-brand-soft">
      {children}
    </Link>
  );
}

export function Dashboard() {
  const { t } = useTranslation();
  const { canSee, session } = useAuth();
  const modules = useMemo(() => (['memory', 'docs', 'flows'] as ModuleKey[]).filter((m) => canSee(m)), [canSee]);
  const [stats, setStats] = useState<Stats>(EMPTY);
  const [breakdown, setBreakdown] = useState<Breakdown>({ memory: [], docs: [], flows: [] });
  const [activity, setActivity] = useState<ActivityByModule | null>(null);
  const [years, setYears] = useState<number[]>([]);
  const [year, setYear] = useState<number | null>(null); // null = rolling last 12 months
  const [yearActivity, setYearActivity] = useState<ActivityByModule | null>(null);
  const [filter, setFilter] = useState<'all' | ModuleKey>('all');

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const [m, d, f, act] = await Promise.all([
        modules.includes('memory') ? tryGet<{ memories_by_context: { context: string; status: string; count: number }[] }>('/memory/stats') : null,
        modules.includes('docs') ? tryGet<{ categories: number; documents: number; documents_by_category: CategoryCount[] }>('/docs/stats') : null,
        modules.includes('flows') ? tryGet<{ flows: number; runs: number; flows_by_category: CategoryCount[] }>('/flows/stats') : null,
        fetchActivity(modules),
      ]);
      if (cancelled) return;
      const active = m?.memories_by_context.filter((r) => r.status === 'active') ?? [];
      setStats({
        memories: m ? active.reduce((a, r) => a + r.count, 0) : null,
        contexts: m ? new Set(active.map((r) => r.context)).size : null,
        documents: d?.documents ?? null,
        categories: d?.categories ?? null,
        flows: f?.flows ?? null,
        runs: f?.runs ?? null,
      });
      const toSlices = (rows: CategoryCount[] | undefined) => topSlices((rows ?? []).map((r) => ({ key: r.slug, name: r.code ? `${r.name} (${r.code})` : r.name, value: r.count })), 5, { key: '__other', name: '' });
      setBreakdown({
        memory: topSlices(active.map((r) => ({ key: r.context, name: r.context, value: r.count })), 5, { key: '__other', name: '' }),
        docs: toSlices(d?.documents_by_category),
        flows: toSlices(f?.flows_by_category),
      });
      setActivity(act.byModule);
      setYears(act.years);
    })();
    return () => {
      cancelled = true;
    };
  }, [modules]);

  useEffect(() => {
    if (year === null) {
      setYearActivity(null);
      return;
    }
    let cancelled = false;
    void fetchActivity(modules, { year }).then((res) => {
      if (!cancelled) setYearActivity(res.byModule);
    });
    return () => {
      cancelled = true;
    };
  }, [year, modules]);

  const heatSource = year === null ? activity : yearActivity;
  const grid = useMemo(() => (year === null ? buildGrid(new Date(), 53) : buildYearGrid(year, new Date())), [year]);
  const shown = filter === 'all' ? modules : modules.filter((m) => m === filter);
  const totals = useMemo(() => (heatSource ? totalsByDate(heatSource, shown) : new Map<string, number>()), [heatSource, shown]);
  const val = (n: number | null) => (n === null ? '—' : n);

  const statsBlock = (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
      {modules.includes('memory') && (
        <CardLink to="/memories">
          <StatCard icon={<Brain className="size-4" />} label={t('dashboard.activeMemories')} value={val(stats.memories)} description={stats.contexts === null ? undefined : t('dashboard.contexts', { count: stats.contexts })} />
        </CardLink>
      )}
      {modules.includes('docs') && (
        <CardLink to="/documents">
          <StatCard icon={<FileText className="size-4" />} label={t('dashboard.documents')} value={val(stats.documents)} description={stats.categories === null ? undefined : t('dashboard.categories', { count: stats.categories })} />
        </CardLink>
      )}
      {modules.includes('flows') && (
        <CardLink to="/workflows">
          <StatCard icon={<Workflow className="size-4" />} label={t('dashboard.flows')} value={val(stats.flows)} description={stats.runs === null ? undefined : t('dashboard.runs', { count: stats.runs })} />
        </CardLink>
      )}
    </div>
  );

  const yearOptions: { value: number | null; label: string }[] = [{ value: null, label: t('dashboard.lastYear') }, ...years.map((y) => ({ value: y, label: String(y) }))];
  const activityBlock = (
    <div className="flex flex-col gap-4 lg:flex-row">
      <section className="min-w-0 flex-1 space-y-3 rounded-2xl border p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold">{t('dashboard.activity')}</h2>
          <div className="flex flex-wrap gap-1">
            {FILTERS.filter((f) => f.key === 'all' || modules.includes(f.key)).map((f) => (
              <Button key={f.key} size="sm" variant={filter === f.key ? 'primary' : 'ghost'} onClick={() => setFilter(f.key)}>
                {t(f.label)}
              </Button>
            ))}
          </div>
        </div>
        {heatSource ? <Heatmap grid={grid} totals={totals} year={year} /> : <p className="text-sm text-muted-foreground">{t('dashboard.loadingActivity')}</p>}
      </section>
      {yearOptions.length > 1 && (
        <section className="space-y-3 rounded-2xl border p-4 lg:w-56 lg:shrink-0">
          <h2 className="text-sm font-semibold">{t('dashboard.years')}</h2>
          <div role="group" aria-label={t('dashboard.years')} className="flex gap-1 overflow-x-auto lg:flex-col lg:overflow-visible">
            {yearOptions.map((o) => (
              <Button key={String(o.value)} size="sm" variant={year === o.value ? 'primary' : 'ghost'} aria-pressed={year === o.value} className="justify-start whitespace-nowrap" onClick={() => setYear(o.value)}>
                {o.label}
              </Button>
            ))}
          </div>
        </section>
      )}
    </div>
  );

  const chartsBlock = activity ? (
    <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-3">
      {modules.includes('memory') && (
        <ModuleChart title={t('charts.memoryTitle')} updatedLabel={t('charts.memoryUpdated')} series={monthlySeries(activity.memory)} slices={breakdown.memory} pieCaption={t('charts.memoryPie')} />
      )}
      {modules.includes('docs') && (
        <ModuleChart title={t('charts.docsTitle')} updatedLabel={t('charts.docsUpdated')} series={monthlySeries(activity.docs)} slices={breakdown.docs} pieCaption={t('charts.docsPie')} />
      )}
      {modules.includes('flows') && (
        <ModuleChart title={t('charts.flowsTitle')} updatedLabel={t('charts.flowsUpdated')} series={monthlySeries(activity.flows)} slices={breakdown.flows} pieCaption={t('charts.flowsPie')} />
      )}
    </div>
  ) : null;

  return (
    <>
      <PageHeader title={t('dashboard.title')} description={t('dashboard.description')} />
      <SortableBlocks
        storageKey={`cerebro.dashboard.order.${session?.name ?? ''}`}
        defaults={['stats', 'activity', 'charts']}
        blocks={{ stats: statsBlock, activity: activityBlock, ...(chartsBlock ? { charts: chartsBlock } : {}) }}
      />
    </>
  );
}
