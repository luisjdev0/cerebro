import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { ArrowDown, ArrowUp, ArrowUpDown, FolderPlus, Plus, Search, X } from 'lucide-react';
import { Badge, Button, PageHeader, Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@alquimias/ixchel';
import { useTranslation } from 'react-i18next';
import { MemoryDetail } from '../components/MemoryDetail';
import { NewContextDialog, NewMemoryDialog } from '../components/MemoryDialogs';
import { statusVariant, typeVariant, useShortDate } from '../components/memoryUi';
import { listContexts, listMemories, MEMORY_STATUSES, MEMORY_TYPES, searchMemories, type Context, type Memory, type SortKey } from '../lib/memories';

const PAGE_SIZES = [10, 20, 50, 100];
const PAGE_SIZE_KEY = 'cerebro.memory.pageSize';
const SELECT = 'h-9 w-full rounded-lg border bg-background px-2.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring';

function loadPageSize(): number {
  try {
    const n = Number(localStorage.getItem(PAGE_SIZE_KEY));
    return PAGE_SIZES.includes(n) ? n : 20;
  } catch {
    return 20;
  }
}

function FilterField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block min-w-0 space-y-1">
      <span className="text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">{label}</span>
      {children}
    </label>
  );
}

function useDebounced<T>(value: T, ms = 350): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const h = setTimeout(() => setV(value), ms);
    return () => clearTimeout(h);
  }, [value, ms]);
  return v;
}

export function Memories() {
  const { t } = useTranslation();
  const date = useShortDate();
  const [params, setParams] = useSearchParams();
  const openId = params.get('id');
  const setOpenId = (id: string | null) =>
    setParams((p) => {
      const n = new URLSearchParams(p);
      if (id) n.set('id', id);
      else n.delete('id');
      return n;
    });

  const [contexts, setContexts] = useState<Context[]>([]);
  const [q, setQ] = useState('');
  const [context, setContext] = useState('');
  const [type, setType] = useState('');
  const [status, setStatus] = useState<'active' | 'superseded' | 'archived' | 'all'>('active');
  const [sort, setSort] = useState<SortKey>('created_at');
  const [order, setOrder] = useState<'asc' | 'desc'>('desc');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSizeState] = useState(loadPageSize);
  const [reloadKey, setReloadKey] = useState(0);
  const [newMemory, setNewMemory] = useState(false);
  const [newContext, setNewContext] = useState(false);
  const [items, setItems] = useState<Memory[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const query = useDebounced(q.trim());
  const searching = query !== '';

  useEffect(() => {
    listContexts().then(setContexts, () => setContexts([]));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(false);
    const run = searching
      ? searchMemories(query, { context: context || undefined, type: type || undefined }).then((r) => ({ items: r, total: r.length }))
      : listMemories({ context: context || undefined, type: type || undefined, status, sort, order, limit: pageSize, offset: (page - 1) * pageSize });
    run.then(
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
  }, [query, searching, context, type, status, sort, order, page, pageSize, reloadKey]);

  const reset = <T,>(set: (v: T) => void) => (v: T) => {
    set(v);
    setPage(1);
  };
  const toggleSort = (key: SortKey) => {
    if (sort === key) setOrder((o) => (o === 'desc' ? 'asc' : 'desc'));
    else {
      setSort(key);
      setOrder('desc');
    }
    setPage(1);
  };
  const setPageSize = (n: number) => {
    setPageSizeState(n);
    setPage(1);
    try {
      localStorage.setItem(PAGE_SIZE_KEY, String(n));
    } catch {
      /* not remembered */
    }
  };
  const filtered = q !== '' || context !== '' || type !== '' || status !== 'active';
  const clearFilters = () => {
    setQ('');
    setContext('');
    setType('');
    setStatus('active');
    setPage(1);
  };
  const SortHead = ({ k, label, className = '' }: { k: SortKey; label: string; className?: string }) => (
    <TableHead className={className}>
      {searching ? (
        label
      ) : (
        <button type="button" onClick={() => toggleSort(k)} className={`inline-flex items-center gap-1 font-medium hover:text-foreground ${sort === k ? 'text-foreground' : ''}`}>
          {label}
          {sort === k ? order === 'desc' ? <ArrowDown className="size-3.5" /> : <ArrowUp className="size-3.5" /> : <ArrowUpDown className="size-3.5 opacity-40" />}
        </button>
      )}
    </TableHead>
  );

  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  const refresh = () => setReloadKey((k) => k + 1);

  return (
    <>
      <PageHeader
        title={t('memory.title')}
        description={t('memory.description')}
        actions={
          <div className="flex flex-wrap gap-2">
            <Button variant="tertiary" leadingIcon={FolderPlus} onClick={() => setNewContext(true)}>{t('memory.actions.newContext')}</Button>
            <Button variant="primary" leadingIcon={Plus} onClick={() => setNewMemory(true)}>{t('memory.actions.newMemory')}</Button>
          </div>
        }
      />

      <div className="space-y-4 rounded-2xl border bg-card p-4">
        <label className="relative block">
          <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <input
            type="search"
            value={q}
            onChange={(e) => reset(setQ)(e.target.value)}
            placeholder={t('memory.searchPlaceholder')}
            aria-label={t('memory.searchPlaceholder')}
            className="h-11 w-full rounded-xl border bg-background pr-3 pl-10 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
        </label>
        <div className="grid grid-cols-1 items-end gap-3 sm:grid-cols-2 lg:grid-cols-[1fr_1fr_1fr_auto]">
          <FilterField label={t('memory.context')}>
            <select className={SELECT} value={context} onChange={(e) => reset(setContext)(e.target.value)}>
              <option value="">{t('memory.allContexts')}</option>
              {contexts.map((c) => (
                <option key={c.slug} value={c.slug}>{c.name}</option>
              ))}
            </select>
          </FilterField>
          <FilterField label={t('memory.type')}>
            <select className={SELECT} value={type} onChange={(e) => reset(setType)(e.target.value)}>
              <option value="">{t('memory.allTypes')}</option>
              {MEMORY_TYPES.map((x) => (
                <option key={x} value={x}>{t(`memory.types.${x}`)}</option>
              ))}
            </select>
          </FilterField>
          <FilterField label={t('memory.status')}>
            <select className={SELECT} disabled={searching} value={status} onChange={(e) => reset(setStatus)(e.target.value as typeof status)}>
              {MEMORY_STATUSES.map((x) => (
                <option key={x} value={x}>{t(`memory.statuses.${x}`)}</option>
              ))}
              <option value="all">{t('memory.allStatuses')}</option>
            </select>
          </FilterField>
          <Button variant="ghost" size="sm" leadingIcon={X} disabled={!filtered} onClick={clearFilters}>
            {t('memory.actions.clearFilters')}
          </Button>
        </div>
      </div>

      <div className="overflow-hidden rounded-2xl border">
        <Table>
          <TableHeader>
            <TableRow>
              <SortHead k="title" label={t('memory.memory')} />
              <SortHead k="context" label={t('memory.context')} />
              <SortHead k="type" label={t('memory.type')} />
              <SortHead k="importance" label={t('memory.importance')} />
              {searching ? <TableHead>{t('memory.relevance')}</TableHead> : <SortHead k="updated_at" label={t('memory.updated')} />}
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              Array.from({ length: 6 }, (_, i) => (
                <TableRow key={i}>
                  <TableCell colSpan={5}><div className="h-8 animate-pulse rounded bg-muted" /></TableCell>
                </TableRow>
              ))
            ) : error || items.length === 0 ? (
              <TableRow>
                <TableCell colSpan={5} className="py-10 text-center text-sm text-muted-foreground">
                  {error ? t('memory.loadError') : searching ? t('memory.emptySearch') : t('memory.empty')}
                </TableCell>
              </TableRow>
            ) : (
              items.map((m) => (
                <TableRow
                  key={m.id}
                  tabIndex={0}
                  onClick={() => setOpenId(m.id)}
                  onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), setOpenId(m.id))}
                  className="cursor-pointer focus-visible:bg-muted focus-visible:outline-none"
                >
                  <TableCell className="max-w-xl">
                    <div className="flex items-center gap-2">
                      <span className="truncate font-medium">{m.title}</span>
                      {m.status !== 'active' && <Badge variant={statusVariant(m.status)}>{t(`memory.statuses.${m.status}`, { defaultValue: m.status })}</Badge>}
                    </div>
                    <p className="line-clamp-1 text-xs text-muted-foreground">{m.content}</p>
                  </TableCell>
                  <TableCell><Badge variant="outline">{m.context}</Badge></TableCell>
                  <TableCell><Badge variant={typeVariant(m.type)}>{t(`memory.types.${m.type}`, { defaultValue: m.type })}</Badge></TableCell>
                  <TableCell className="tabular-nums">{m.importance.toFixed(2)}</TableCell>
                  <TableCell className="whitespace-nowrap text-muted-foreground tabular-nums">
                    {searching ? `${Math.round((m.score ?? 0) * 100)}%` : date(m.updated_at)}
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-muted-foreground">
        <span>{loading || error ? '' : searching ? t('memory.searchResults', { count: total }) : t('memory.range', { from, to, total })}</span>
        {!searching && (
          <div className="flex flex-wrap items-center gap-4">
            <label className="flex items-center gap-2">
              <span>{t('memory.pageSize')}</span>
              <select className="h-8 rounded-lg border bg-background px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring" value={pageSize} onChange={(e) => setPageSize(Number(e.target.value))}>
                {PAGE_SIZES.map((n) => (
                  <option key={n} value={n}>{n}</option>
                ))}
              </select>
            </label>
            <div className="flex items-center gap-2">
              <Button size="sm" variant="ghost" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>{t('memory.previous')}</Button>
              <span>{t('memory.page', { page, pages })}</span>
              <Button size="sm" variant="ghost" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>{t('memory.next')}</Button>
            </div>
          </div>
        )}
      </div>

      <MemoryDetail id={openId} onOpen={setOpenId} onClose={() => setOpenId(null)} onChanged={refresh} />
      <NewMemoryDialog
        open={newMemory}
        contexts={contexts}
        defaultContext={context || undefined}
        onClose={() => setNewMemory(false)}
        onCreated={(m) => {
          refresh();
          setOpenId(m.id);
        }}
        onNewContext={() => setNewContext(true)}
      />
      <NewContextDialog open={newContext} onClose={() => setNewContext(false)} onCreated={(c) => setContexts((cs) => [...cs, c].sort((x, y) => x.name.localeCompare(y.name)))} />
    </>
  );
}
