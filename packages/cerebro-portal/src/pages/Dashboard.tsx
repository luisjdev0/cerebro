import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Brain, FileText, Workflow } from 'lucide-react';
import { Button, PageHeader, StatCard } from '@alquimias/ixchel';
import { Heatmap } from '../components/Heatmap';
import { api } from '../lib/api';
import { useAuth } from '../lib/auth';
import { fetchActivity, totalsByDate, type ActivityByModule, type ModuleKey } from '../lib/activity';

type Stats = {
  memories: number | null;
  contexts: number | null;
  documents: number | null;
  categories: number | null;
  flows: number | null;
  runs: number | null;
};

const EMPTY: Stats = { memories: null, contexts: null, documents: null, categories: null, flows: null, runs: null };
const FILTERS: { key: 'all' | ModuleKey; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'memory', label: 'Memory' },
  { key: 'docs', label: 'Documents' },
  { key: 'flows', label: 'Flows' },
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
  const { canSee } = useAuth();
  const modules = useMemo(() => (['memory', 'docs', 'flows'] as ModuleKey[]).filter((m) => canSee(m)), [canSee]);
  const [stats, setStats] = useState<Stats>(EMPTY);
  const [activity, setActivity] = useState<ActivityByModule | null>(null);
  const [filter, setFilter] = useState<'all' | ModuleKey>('all');

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const [m, d, f, act] = await Promise.all([
        modules.includes('memory') ? tryGet<{ memories_by_context: { context: string; status: string; count: number }[] }>('/memory/stats') : null,
        modules.includes('docs') ? tryGet<{ categories: number; documents: number }>('/docs/stats') : null,
        modules.includes('flows') ? tryGet<{ flows: number; runs: number }>('/flows/stats') : null,
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
      setActivity(act);
    })();
    return () => {
      cancelled = true;
    };
  }, [modules]);

  const shown = filter === 'all' ? modules : modules.filter((m) => m === filter);
  const totals = useMemo(() => (activity ? totalsByDate(activity, shown) : new Map<string, number>()), [activity, shown]);
  const val = (n: number | null) => (n === null ? '—' : n);

  return (
    <>
      <PageHeader title="Overview" description="State of your cerebro instance" />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {modules.includes('memory') && (
          <CardLink to="/memories">
            <StatCard icon={<Brain className="size-4" />} label="Active memories" value={val(stats.memories)} description={stats.contexts === null ? undefined : `${stats.contexts} contexts`} />
          </CardLink>
        )}
        {modules.includes('docs') && (
          <CardLink to="/documents">
            <StatCard icon={<FileText className="size-4" />} label="Documents" value={val(stats.documents)} description={stats.categories === null ? undefined : `${stats.categories} categories`} />
          </CardLink>
        )}
        {modules.includes('flows') && (
          <CardLink to="/workflows">
            <StatCard icon={<Workflow className="size-4" />} label="Flows" value={val(stats.flows)} description={stats.runs === null ? undefined : `${stats.runs} runs`} />
          </CardLink>
        )}
      </div>

      <section className="space-y-3 rounded-2xl border p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold">Activity</h2>
          <div className="flex gap-1">
            {FILTERS.filter((f) => f.key === 'all' || modules.includes(f.key)).map((f) => (
              <Button key={f.key} size="sm" variant={filter === f.key ? 'primary' : 'ghost'} onClick={() => setFilter(f.key)}>
                {f.label}
              </Button>
            ))}
          </div>
        </div>
        {activity ? <Heatmap totals={totals} /> : <p className="text-sm text-muted-foreground">Loading activity…</p>}
      </section>
    </>
  );
}
