import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { ArrowDown, ArrowUp, ArrowUpDown, FolderCog, Plus, Search, X } from 'lucide-react';
import { Badge, Button, PageHeader, Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@alquimias/ixchel';
import { useTranslation } from 'react-i18next';
import { CategoriesPanel, NewDocumentPanel } from '../components/DocumentPanels';
import { DocumentDetail, type DocRef } from '../components/DocumentDetail';
import { useShortDate } from '../components/memoryUi';
import { browseDocuments, listCategories, plainExcerpt, type Category, type DocSortKey, type DocumentSummary } from '../lib/documents';

const PAGE_SIZES = [10, 20, 50, 100];
const PAGE_SIZE_KEY = 'cerebro.docs.pageSize';
const SELECT = 'h-9 w-full rounded-lg border bg-background px-2.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring';

function loadPageSize(): number {
  try {
    const n = Number(localStorage.getItem(PAGE_SIZE_KEY));
    return PAGE_SIZES.includes(n) ? n : 10;
  } catch {
    return 10;
  }
}

/** Nearest ancestor that actually scrolls (the app shell scrolls, not the window). */
function scrollParent(el: HTMLElement): HTMLElement {
  for (let n = el.parentElement; n; n = n.parentElement) {
    const o = getComputedStyle(n).overflowY;
    if ((o === 'auto' || o === 'scroll') && n.scrollHeight > n.clientHeight) return n;
  }
  return (document.scrollingElement as HTMLElement) ?? document.documentElement;
}

function FilterField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block min-w-0 space-y-1">
      <span className="text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">{label}</span>
      {children}
    </label>
  );
}

/** The excerpt without the document's own title when its content starts with it. */
function bodyExcerpt(d: DocumentSummary): string {
  const ex = plainExcerpt(d.excerpt);
  return ex.toLowerCase().startsWith(d.title.toLowerCase()) ? ex.slice(d.title.length).trim() : ex;
}

function useDebounced<T>(value: T, ms = 350): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const h = setTimeout(() => setV(value), ms);
    return () => clearTimeout(h);
  }, [value, ms]);
  return v;
}

export function Documents() {
  const { t } = useTranslation();
  const date = useShortDate();
  const [params, setParams] = useSearchParams();
  const rawRef = params.get('doc');
  const openRef: DocRef | null = rawRef && rawRef.includes('/') ? { category: rawRef.split('/')[0], slug: rawRef.split('/').slice(1).join('/') } : null;
  const setOpenRef = (r: DocRef | null) =>
    setParams((p) => {
      const n = new URLSearchParams(p);
      if (r) n.set('doc', `${r.category}/${r.slug}`);
      else n.delete('doc');
      return n;
    });

  const [categories, setCategories] = useState<Category[]>([]);
  const [q, setQ] = useState('');
  const [category, setCategory] = useState('');
  const [status, setStatus] = useState<'active' | 'archived' | 'all'>('active');
  const [sort, setSort] = useState<DocSortKey>('updated_at');
  const [order, setOrder] = useState<'asc' | 'desc'>('desc');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSizeState] = useState(loadPageSize);
  const [reloadKey, setReloadKey] = useState(0);
  const [newDoc, setNewDoc] = useState(false);
  const [manageCats, setManageCats] = useState(false);
  const [createCatFirst, setCreateCatFirst] = useState(false);
  const [items, setItems] = useState<DocumentSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const barRef = useRef<HTMLDivElement>(null);
  const tableRef = useRef<HTMLDivElement>(null);
  const anchorTop = useRef<number | null>(null);

  const query = useDebounced(q.trim());
  const searching = query !== '';

  const reloadCategories = () =>
    listCategories().then(
      (cs) => {
        setCategories(cs);
        setCategory((cur) => (cs.some((c) => c.slug === cur) ? cur : ''));
      },
      () => setCategories([]),
    );
  useEffect(() => {
    void reloadCategories();
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(false);
    browseDocuments({ category: category || undefined, q: query || undefined, status, sort, order, limit: pageSize, offset: (page - 1) * pageSize }).then(
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
  }, [query, category, status, sort, order, page, pageSize, reloadKey]);

  // The table never gets shorter while paging, and the bar is re-anchored after each page change,
  // so the pagination buttons stay under the pointer (same behavior as the Memory screen).
  useLayoutEffect(() => {
    const el = tableRef.current;
    if (!el || loading) return;
    const h = el.offsetHeight;
    if (h > (parseFloat(el.style.minHeight) || 0)) el.style.minHeight = `${h}px`;
  }, [loading, items]);
  const resetTableHeight = () => {
    if (tableRef.current) tableRef.current.style.minHeight = '';
  };
  const holdBar = () => {
    anchorTop.current = barRef.current?.getBoundingClientRect().top ?? null;
  };
  useLayoutEffect(() => {
    const bar = barRef.current;
    if (loading || anchorTop.current === null || !bar) return;
    const delta = bar.getBoundingClientRect().top - anchorTop.current;
    anchorTop.current = null;
    if (delta !== 0) scrollParent(bar).scrollBy({ top: delta, behavior: 'instant' });
  }, [loading, items]);

  const reset = <T,>(set: (v: T) => void) => (v: T) => {
    resetTableHeight();
    set(v);
    setPage(1);
  };
  const toggleSort = (key: DocSortKey) => {
    if (sort === key) setOrder((o) => (o === 'desc' ? 'asc' : 'desc'));
    else {
      setSort(key);
      setOrder(key === 'title' || key === 'category' ? 'asc' : 'desc');
    }
    setPage(1);
  };
  const setPageSize = (n: number) => {
    resetTableHeight();
    setPageSizeState(n);
    setPage(1);
    try {
      localStorage.setItem(PAGE_SIZE_KEY, String(n));
    } catch {
      /* not remembered */
    }
  };
  const filtered = q !== '' || category !== '' || status !== 'active';
  const clearFilters = () => {
    resetTableHeight();
    setQ('');
    setCategory('');
    setStatus('active');
    setPage(1);
  };
  const refresh = () => setReloadKey((k) => k + 1);

  const SortHead = ({ k, label }: { k: DocSortKey; label: string }) => (
    <TableHead>
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

  return (
    <>
      <PageHeader
        title={t('docs.title')}
        description={t('docs.description')}
        actions={
          <div className="flex flex-wrap gap-2">
            <Button variant="tertiary" leadingIcon={FolderCog} onClick={() => { setCreateCatFirst(false); setManageCats(true); }}>{t('docs.actions.manageCategories')}</Button>
            <Button variant="primary" leadingIcon={Plus} onClick={() => setNewDoc(true)}>{t('docs.actions.newDocument')}</Button>
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
            placeholder={t('docs.searchPlaceholder')}
            aria-label={t('docs.searchPlaceholder')}
            className="h-11 w-full rounded-xl border bg-background pr-3 pl-10 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
        </label>
        <div className="grid grid-cols-1 items-end gap-3 sm:grid-cols-2 lg:grid-cols-[1fr_1fr_auto]">
          <FilterField label={t('docs.category')}>
            <select className={SELECT} value={category} onChange={(e) => reset(setCategory)(e.target.value)}>
              <option value="">{t('docs.allCategories')}</option>
              {categories.map((c) => (
                <option key={c.slug} value={c.slug}>{c.name}</option>
              ))}
            </select>
          </FilterField>
          <FilterField label={t('docs.status')}>
            <select className={SELECT} value={status} onChange={(e) => reset(setStatus)(e.target.value as typeof status)}>
              <option value="active">{t('docs.statuses.active')}</option>
              <option value="archived">{t('docs.statuses.archived')}</option>
              <option value="all">{t('docs.statuses.all')}</option>
            </select>
          </FilterField>
          <Button variant="ghost" size="sm" leadingIcon={X} disabled={!filtered} onClick={clearFilters}>{t('docs.actions.clearFilters')}</Button>
        </div>
      </div>

      <div ref={tableRef} className="overflow-hidden rounded-2xl border">
        <Table>
          <TableHeader>
            <TableRow>
              <SortHead k="title" label={t('docs.document')} />
              <SortHead k="category" label={t('docs.category')} />
              {searching ? <TableHead>{t('docs.relevance')}</TableHead> : <SortHead k="updated_at" label={t('docs.updated')} />}
            </TableRow>
          </TableHeader>
          <TableBody className={loading ? 'opacity-60 transition-opacity' : 'transition-opacity'}>
            {loading && items.length === 0 ? (
              Array.from({ length: pageSize }, (_, i) => (
                <TableRow key={i}>
                  <TableCell colSpan={3}><div className="h-8 animate-pulse rounded bg-muted" /></TableCell>
                </TableRow>
              ))
            ) : (error || items.length === 0) && !loading ? (
              <TableRow>
                <TableCell colSpan={3} className="py-10 text-center text-sm text-muted-foreground">
                  {error ? t('docs.loadError') : searching ? t('docs.emptySearch') : t('docs.empty')}
                </TableCell>
              </TableRow>
            ) : (
              items.map((d) => (
                <TableRow
                  key={d.id}
                  tabIndex={0}
                  onClick={() => setOpenRef({ category: d.category, slug: d.slug })}
                  onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), setOpenRef({ category: d.category, slug: d.slug }))}
                  className="cursor-pointer focus-visible:bg-muted focus-visible:outline-none"
                >
                  <TableCell className="max-w-xl">
                    <div className="flex items-center gap-2">
                      <span className="truncate font-medium">{d.title}</span>
                      {d.status === 'archived' && <Badge variant="warning">{t('docs.statuses.archived')}</Badge>}
                    </div>
                    <p className="line-clamp-1 text-xs text-muted-foreground">{bodyExcerpt(d)}</p>
                  </TableCell>
                  <TableCell><Badge variant="outline">{d.category}</Badge></TableCell>
                  <TableCell className="whitespace-nowrap text-muted-foreground tabular-nums">
                    {searching ? `${Math.round(Math.min(1, d.score ?? 0) * 100)}%` : date(d.updated_at)}
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      <div ref={barRef} className="flex flex-wrap items-center justify-between gap-3 text-sm text-muted-foreground">
        <span>{loading || error ? '' : searching ? t('docs.searchResults', { count: total }) : t('docs.range', { from, to, total })}</span>
        <div className="flex flex-wrap items-center gap-4">
          <label className="flex items-center gap-2">
            <span>{t('docs.pageSize')}</span>
            <select className="h-8 rounded-lg border bg-background px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring" value={pageSize} onChange={(e) => { holdBar(); setPageSize(Number(e.target.value)); }}>
              {PAGE_SIZES.map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
          </label>
          <div className="flex items-center gap-2">
            <Button size="sm" variant="ghost" disabled={page <= 1} onClick={() => { holdBar(); setPage((p) => p - 1); }}>{t('docs.previous')}</Button>
            <span>{t('docs.page', { page, pages })}</span>
            <Button size="sm" variant="ghost" disabled={page >= pages} onClick={() => { holdBar(); setPage((p) => p + 1); }}>{t('docs.next')}</Button>
          </div>
        </div>
      </div>

      <DocumentDetail docRef={openRef} categories={categories} onOpen={setOpenRef} onClose={() => setOpenRef(null)} onChanged={refresh} />
      <CategoriesPanel
        open={manageCats}
        categories={categories}
        startCreating={createCatFirst}
        onClose={() => {
          setManageCats(false);
          setCreateCatFirst(false);
        }}
        onChanged={() => {
          setCreateCatFirst(false);
          void reloadCategories();
          refresh();
        }}
      />
      <NewDocumentPanel
        open={newDoc}
        categories={categories}
        defaultCategory={category || undefined}
        onClose={() => setNewDoc(false)}
        onCreated={(d) => {
          refresh();
          setOpenRef({ category: d.category, slug: d.slug });
        }}
        onImported={() => {
          refresh();
          void reloadCategories();
        }}
        onNewCategory={() => {
          setNewDoc(false);
          setCreateCatFirst(true);
          setManageCats(true);
        }}
      />
    </>
  );
}
