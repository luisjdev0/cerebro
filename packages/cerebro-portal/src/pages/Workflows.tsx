import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { ArrowDown, ArrowUp, ArrowUpDown, Plus, Search, X } from 'lucide-react';
import { Badge, Button, PageHeader, Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@alquimias/ixchel';
import { useTranslation } from 'react-i18next';
import { FlowDetail, NewFlowPanel } from '../components/FlowPanels';
import { FilterField, loadPageSize, PAGE_SIZES, RunStatusBadge, SELECT, useDebounced } from '../components/flowUi';
import { useDateTime, useShortDate } from '../components/memoryUi';
import { RunDetail } from '../components/RunDetail';
import { useTableAnchor } from '../components/useTableAnchor';
import {
  browseFlows,
  browseRuns,
  listFlowCategories,
  type FlowCategory,
  type FlowSortKey,
  type FlowSummary,
  type RunSortKey,
  type RunStatus,
  type RunSummary,
} from '../lib/flows';

type Tab = 'flows' | 'runs';

/** Sortable column header: a plain label while searching (results are ranked, not sorted). */
function SortHead<K extends string>({ k, label, sort, order, onSort, disabled }: { k: K; label: string; sort: K; order: 'asc' | 'desc'; onSort: (k: K) => void; disabled?: boolean }) {
  return (
    <TableHead>
      {disabled ? (
        label
      ) : (
        <button type="button" onClick={() => onSort(k)} className={`inline-flex items-center gap-1 font-medium hover:text-foreground ${sort === k ? 'text-foreground' : ''}`}>
          {label}
          {sort === k ? order === 'desc' ? <ArrowDown className="size-3.5" /> : <ArrowUp className="size-3.5" /> : <ArrowUpDown className="size-3.5 opacity-40" />}
        </button>
      )}
    </TableHead>
  );
}

function Pager({ barRef, loading, error, total, page, pageSize, onPage, onPageSize, holdBar }: {
  barRef: React.RefObject<HTMLDivElement | null>;
  loading: boolean;
  error: boolean;
  total: number;
  page: number;
  pageSize: number;
  onPage: (p: number) => void;
  onPageSize: (n: number) => void;
  holdBar: () => void;
}) {
  const { t } = useTranslation();
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  return (
    <div ref={barRef} className="flex flex-wrap items-center justify-between gap-3 text-sm text-muted-foreground">
      <span>{loading || error ? '' : t('docs.range', { from, to, total })}</span>
      <div className="flex flex-wrap items-center gap-4">
        <label className="flex items-center gap-2">
          <span>{t('docs.pageSize')}</span>
          <select className="h-8 rounded-lg border bg-background px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring" value={pageSize} onChange={(e) => { holdBar(); onPageSize(Number(e.target.value)); }}>
            {PAGE_SIZES.map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
        </label>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="ghost" disabled={page <= 1} onClick={() => { holdBar(); onPage(page - 1); }}>{t('docs.previous')}</Button>
          <span>{t('docs.page', { page, pages })}</span>
          <Button size="sm" variant="ghost" disabled={page >= pages} onClick={() => { holdBar(); onPage(page + 1); }}>{t('docs.next')}</Button>
        </div>
      </div>
    </div>
  );
}

function EmptyRow({ cols, loading, error, message, rows }: { cols: number; loading: boolean; error: boolean; message: string; rows: number }) {
  const { t } = useTranslation();
  if (loading) {
    return (
      <>
        {Array.from({ length: rows }, (_, i) => (
          <TableRow key={i}>
            <TableCell colSpan={cols}><div className="h-8 animate-pulse rounded bg-muted" /></TableCell>
          </TableRow>
        ))}
      </>
    );
  }
  return (
    <TableRow>
      <TableCell colSpan={cols} className="py-10 text-center text-sm text-muted-foreground">{error ? t('flows.loadError') : message}</TableCell>
    </TableRow>
  );
}

function FlowsTab({ categories, reloadCategories, onChanged, changeKey }: { categories: FlowCategory[]; reloadCategories: () => void; onChanged: () => void; changeKey: number }) {
  const { t } = useTranslation();
  const date = useShortDate();
  const [params, setParams] = useSearchParams();
  const openCode = params.get('flow');
  const setOpenCode = (c: string | null) =>
    setParams((p) => {
      const n = new URLSearchParams(p);
      if (c) n.set('flow', c);
      else n.delete('flow');
      return n;
    });

  const [q, setQ] = useState('');
  const [category, setCategory] = useState('');
  const [sort, setSort] = useState<FlowSortKey>('updated_at');
  const [order, setOrder] = useState<'asc' | 'desc'>('desc');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSizeState] = useState(() => loadPageSize('cerebro.flows.pageSize'));
  const [reloadKey, setReloadKey] = useState(0);
  const [newFlow, setNewFlow] = useState(false);
  const [items, setItems] = useState<FlowSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const query = useDebounced(q.trim());
  const { barRef, tableRef, resetTableHeight, holdBar } = useTableAnchor(loading, items);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(false);
    browseFlows({ category: category || undefined, q: query || undefined, sort, order, limit: pageSize, offset: (page - 1) * pageSize }).then(
      (r) => {
        if (cancelled) return;
        setItems(r.items);
        setTotal(r.total);
        setLoading(false);
      },
      () => {
        if (cancelled) return;
        setError(true);
        setItems([]);
        setLoading(false);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [query, category, sort, order, page, pageSize, reloadKey, changeKey]);

  const reset = <T,>(set: (v: T) => void) => (v: T) => {
    resetTableHeight();
    set(v);
    setPage(1);
  };
  const toggleSort = (key: FlowSortKey) => {
    if (sort === key) setOrder((o) => (o === 'desc' ? 'asc' : 'desc'));
    else {
      setSort(key);
      setOrder(key === 'code' || key === 'name' || key === 'category' ? 'asc' : 'desc');
    }
    setPage(1);
  };
  const setPageSize = (n: number) => {
    resetTableHeight();
    setPageSizeState(n);
    setPage(1);
    try {
      localStorage.setItem('cerebro.flows.pageSize', String(n));
    } catch {
      /* not remembered */
    }
  };
  const filtered = q !== '' || category !== '';
  const sortProps = { sort, order, onSort: toggleSort };

  return (
    <>
      <div className="flex justify-end">
        <Button variant="primary" leadingIcon={Plus} onClick={() => setNewFlow(true)}>{t('flows.actions.newFlow')}</Button>
      </div>
      <div className="space-y-4 rounded-2xl border bg-card p-4">
        <label className="relative block">
          <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <input
            type="search"
            value={q}
            onChange={(e) => reset(setQ)(e.target.value)}
            placeholder={t('flows.searchPlaceholder')}
            aria-label={t('flows.searchPlaceholder')}
            className="h-11 w-full rounded-xl border bg-background pr-3 pl-10 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
        </label>
        <div className="grid grid-cols-1 items-end gap-3 sm:grid-cols-[1fr_auto]">
          <FilterField label={t('docs.category')}>
            <select className={SELECT} value={category} onChange={(e) => reset(setCategory)(e.target.value)}>
              <option value="">{t('docs.allCategories')}</option>
              {categories.map((c) => (
                <option key={c.slug} value={c.slug}>{c.name}</option>
              ))}
            </select>
          </FilterField>
          <Button variant="ghost" size="sm" leadingIcon={X} disabled={!filtered} onClick={() => { resetTableHeight(); setQ(''); setCategory(''); setPage(1); }}>{t('docs.actions.clearFilters')}</Button>
        </div>
      </div>

      <div ref={tableRef} className="overflow-hidden rounded-2xl border">
        <Table>
          <TableHeader>
            <TableRow>
              <SortHead k="name" label={t('flows.flow')} {...sortProps} />
              <SortHead k="category" label={t('docs.category')} {...sortProps} />
              <TableHead>{t('flows.version')}</TableHead>
              <SortHead k="updated_at" label={t('docs.updated')} {...sortProps} />
            </TableRow>
          </TableHeader>
          <TableBody className={loading ? 'opacity-60 transition-opacity' : 'transition-opacity'}>
            {(loading && items.length === 0) || ((error || items.length === 0) && !loading) ? (
              <EmptyRow cols={4} loading={loading && items.length === 0} error={error} rows={pageSize} message={query ? t('flows.emptySearch') : t('flows.empty')} />
            ) : (
              items.map((f) => (
                <TableRow
                  key={f.id}
                  tabIndex={0}
                  onClick={() => setOpenCode(f.code)}
                  onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), setOpenCode(f.code))}
                  className="cursor-pointer focus-visible:bg-muted focus-visible:outline-none"
                >
                  <TableCell className="max-w-xl">
                    <div className="flex items-center gap-2">
                      <Badge variant="secondary" className="font-mono">{f.code}</Badge>
                      <span className="truncate font-medium">{f.name}</span>
                    </div>
                    {f.description && <p className="line-clamp-1 text-xs text-muted-foreground">{f.description}</p>}
                  </TableCell>
                  <TableCell><Badge variant="outline">{f.category}</Badge></TableCell>
                  <TableCell className="text-muted-foreground tabular-nums">v{f.current_version}</TableCell>
                  <TableCell className="whitespace-nowrap text-muted-foreground tabular-nums">{date(f.updated_at)}</TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>
      <Pager barRef={barRef} loading={loading} error={error} total={total} page={page} pageSize={pageSize} onPage={setPage} onPageSize={setPageSize} holdBar={holdBar} />

      <FlowDetail code={openCode} onClose={() => setOpenCode(null)} onChanged={() => { setReloadKey((k) => k + 1); onChanged(); }} />
      <NewFlowPanel
        open={newFlow}
        categories={categories}
        defaultCategory={category || undefined}
        onClose={() => setNewFlow(false)}
        onCategoryCreated={reloadCategories}
        onCreated={(f) => {
          setReloadKey((k) => k + 1);
          onChanged();
          setOpenCode(f.code);
        }}
      />
    </>
  );
}

function RunsTab({ categories, onChanged, changeKey }: { categories: FlowCategory[]; onChanged: () => void; changeKey: number }) {
  const { t } = useTranslation();
  const dt = useDateTime();
  const [status, setStatus] = useState<RunStatus | 'all'>('all');
  const [awaiting, setAwaiting] = useState(false);
  const [category, setCategory] = useState('');
  const [sort, setSort] = useState<RunSortKey>('started_at');
  const [order, setOrder] = useState<'asc' | 'desc'>('desc');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSizeState] = useState(() => loadPageSize('cerebro.runs.pageSize'));
  const [reloadKey, setReloadKey] = useState(0);
  const [items, setItems] = useState<RunSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const [opened, setOpened] = useState<RunSummary | null>(null);
  const { barRef, tableRef, resetTableHeight, holdBar } = useTableAnchor(loading, items);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(false);
    browseRuns({ status, awaiting, category: category || undefined, sort, order, limit: pageSize, offset: (page - 1) * pageSize }).then(
      (r) => {
        if (cancelled) return;
        setItems(r.items);
        setTotal(r.total);
        setLoading(false);
      },
      () => {
        if (cancelled) return;
        setError(true);
        setItems([]);
        setLoading(false);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [status, awaiting, category, sort, order, page, pageSize, reloadKey, changeKey]);

  // The open panel follows its row when the list reloads (after approving, say); it keeps the last known state otherwise.
  const open = openId ? items.find((r) => r.run_id === openId) ?? (opened?.run_id === openId ? opened : null) : null;

  const reset = <T,>(set: (v: T) => void) => (v: T) => {
    resetTableHeight();
    set(v);
    setPage(1);
  };
  const toggleSort = (key: RunSortKey) => {
    if (sort === key) setOrder((o) => (o === 'desc' ? 'asc' : 'desc'));
    else {
      setSort(key);
      setOrder(key === 'flow_code' || key === 'status' ? 'asc' : 'desc');
    }
    setPage(1);
  };
  const setPageSize = (n: number) => {
    resetTableHeight();
    setPageSizeState(n);
    setPage(1);
    try {
      localStorage.setItem('cerebro.runs.pageSize', String(n));
    } catch {
      /* not remembered */
    }
  };
  const filtered = status !== 'all' || awaiting || category !== '';
  // "Awaiting approval" is a view of in-progress runs sorted by age: the column sort does not apply to it.
  const sortProps = { sort, order, onSort: toggleSort, disabled: awaiting };
  const refresh = () => {
    setReloadKey((k) => k + 1);
    onChanged();
  };

  return (
    <>
      <div className="space-y-4 rounded-2xl border bg-card p-4">
        <div className="grid grid-cols-1 items-end gap-3 sm:grid-cols-2 lg:grid-cols-[1fr_1fr_auto_auto]">
          <FilterField label={t('docs.category')}>
            <select className={SELECT} value={category} onChange={(e) => reset(setCategory)(e.target.value)}>
              <option value="">{t('docs.allCategories')}</option>
              {categories.map((c) => (
                <option key={c.slug} value={c.slug}>{c.name}</option>
              ))}
            </select>
          </FilterField>
          <FilterField label={t('docs.status')}>
            <select className={SELECT} value={awaiting ? 'in_progress' : status} disabled={awaiting} onChange={(e) => reset(setStatus)(e.target.value as RunStatus | 'all')}>
              <option value="all">{t('flows.runStatus.all')}</option>
              <option value="in_progress">{t('flows.runStatus.in_progress')}</option>
              <option value="completed">{t('flows.runStatus.completed')}</option>
              <option value="aborted">{t('flows.runStatus.aborted')}</option>
            </select>
          </FilterField>
          <label className="flex h-9 items-center gap-2 text-sm">
            <input type="checkbox" className="size-4 accent-[var(--color-brand-soft,currentColor)]" checked={awaiting} onChange={(e) => reset(setAwaiting)(e.target.checked)} />
            {t('flows.runs.onlyAwaiting')}
          </label>
          <Button variant="ghost" size="sm" leadingIcon={X} disabled={!filtered} onClick={() => { resetTableHeight(); setStatus('all'); setAwaiting(false); setCategory(''); setPage(1); }}>{t('docs.actions.clearFilters')}</Button>
        </div>
      </div>

      <div ref={tableRef} className="overflow-hidden rounded-2xl border">
        <Table>
          <TableHeader>
            <TableRow>
              <SortHead k="flow_code" label={t('flows.flow')} {...sortProps} />
              <SortHead k="status" label={t('docs.status')} {...sortProps} />
              <TableHead>{t('flows.runs.step')}</TableHead>
              <SortHead k="started_at" label={t('flows.runs.started')} {...sortProps} />
            </TableRow>
          </TableHeader>
          <TableBody className={loading ? 'opacity-60 transition-opacity' : 'transition-opacity'}>
            {(loading && items.length === 0) || ((error || items.length === 0) && !loading) ? (
              <EmptyRow cols={4} loading={loading && items.length === 0} error={error} rows={pageSize} message={awaiting ? t('flows.runs.noneAwaiting') : t('flows.runs.empty')} />
            ) : (
              items.map((r) => (
                <TableRow
                  key={r.run_id}
                  tabIndex={0}
                  onClick={() => { setOpened(r); setOpenId(r.run_id); }}
                  onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), setOpened(r), setOpenId(r.run_id))}
                  className="cursor-pointer focus-visible:bg-muted focus-visible:outline-none"
                >
                  <TableCell className="max-w-xl">
                    <div className="flex items-center gap-2">
                      <Badge variant="secondary" className="font-mono">{r.flow_code}</Badge>
                      <span className="truncate font-medium">{r.flow_name}</span>
                    </div>
                    <p className="font-mono text-[11px] text-muted-foreground">{r.run_id}</p>
                  </TableCell>
                  <TableCell><RunStatusBadge run={r} /></TableCell>
                  <TableCell className="font-mono text-xs text-muted-foreground">{r.current_step_id ?? '—'}</TableCell>
                  <TableCell className="whitespace-nowrap text-muted-foreground tabular-nums">{dt(r.started_at)}</TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>
      <Pager barRef={barRef} loading={loading} error={error} total={total} page={page} pageSize={pageSize} onPage={setPage} onPageSize={setPageSize} holdBar={holdBar} />

      <RunDetail run={open} onClose={() => setOpenId(null)} onChanged={refresh} />
    </>
  );
}

export function Workflows() {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const tab: Tab = params.get('tab') === 'runs' ? 'runs' : 'flows';
  const setTab = (next: Tab) =>
    setParams((p) => {
      const n = new URLSearchParams(p);
      n.delete('flow');
      if (next === 'runs') n.set('tab', 'runs');
      else n.delete('tab');
      return n;
    });

  const [categories, setCategories] = useState<FlowCategory[]>([]);
  const [pending, setPending] = useState(0);
  const [changeKey, setChangeKey] = useState(0);
  const reloadCategories = () => {
    listFlowCategories().then(setCategories, () => setCategories([]));
  };
  useEffect(reloadCategories, []);

  // Runs stopped at a checkpoint nobody approved: the number a person actually has to act on.
  useEffect(() => {
    let cancelled = false;
    browseRuns({ status: 'in_progress', awaiting: true, sort: 'started_at', order: 'desc', limit: 1, offset: 0 }).then(
      (r) => !cancelled && setPending(r.total),
      () => !cancelled && setPending(0),
    );
    return () => {
      cancelled = true;
    };
  }, [changeKey, tab]);
  const bump = () => setChangeKey((k) => k + 1);

  const TabButton = ({ id, label, badge }: { id: Tab; label: string; badge?: number }) => (
    <button
      type="button"
      role="tab"
      aria-selected={tab === id}
      onClick={() => setTab(id)}
      className={`inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-colors ${tab === id ? 'bg-foreground text-background' : 'text-muted-foreground hover:text-foreground'}`}
    >
      {label}
      {badge ? <span className="inline-flex min-w-5 items-center justify-center rounded-full bg-amber-500 px-1.5 text-[11px] font-bold text-black">{badge}</span> : null}
    </button>
  );

  return (
    <>
      <PageHeader title={t('flows.title')} description={t('flows.description')} />
      <div role="tablist" className="inline-flex gap-1 rounded-xl border p-1">
        <TabButton id="flows" label={t('flows.tabs.flows')} />
        <TabButton id="runs" label={t('flows.tabs.runs')} badge={pending} />
      </div>
      {tab === 'flows' ? (
        <FlowsTab categories={categories} reloadCategories={reloadCategories} onChanged={bump} changeKey={changeKey} />
      ) : (
        <RunsTab categories={categories} onChanged={bump} changeKey={changeKey} />
      )}
    </>
  );
}
