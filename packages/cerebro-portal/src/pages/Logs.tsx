import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Download, RefreshCw, Search } from 'lucide-react';
import { Button, PageHeader } from '@alquimias/ixchel';
import { useTranslation } from 'react-i18next';
import { CopyButton } from '../components/MarkdownView';
import { useAdminError } from '../components/adminUi';
import { useAuth } from '../lib/auth';
import { LOG_SOURCES, lineToText, type LogLine, type LogSource } from '../lib/logs';

const LIVE_MS = 5000;

function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }));
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function LogView({ source }: { source: LogSource }) {
  const { t } = useTranslation();
  const errMsg = useAdminError();
  const [lines, setLines] = useState<LogLine[] | null>(null);
  const [next, setNext] = useState<string | null>(null);
  const [error, setError] = useState<string>();
  const [loadingMore, setLoadingMore] = useState(false);
  const [live, setLive] = useState(false);
  const [q, setQ] = useState('');
  const known = useRef(new Set<string>());

  const refresh = useCallback(async () => {
    try {
      const page = await source.fetch();
      setError(undefined);
      setLines((prev) => {
        if (prev === null) {
          known.current = new Set(page.lines.map((l) => l.key));
          setNext(page.next);
          return page.lines;
        }
        const fresh = page.lines.filter((l) => !known.current.has(l.key)); // newer than anything we hold
        fresh.forEach((l) => known.current.add(l.key));
        return fresh.length ? [...fresh, ...prev] : prev;
      });
    } catch (e) {
      setError(errMsg(e));
      setLines((prev) => prev ?? []);
    }
  }, [source]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    setLines(null);
    setNext(null);
    setLive(false);
    known.current = new Set();
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!live) return;
    const id = window.setInterval(() => void refresh(), LIVE_MS);
    return () => window.clearInterval(id);
  }, [live, refresh]);

  const more = async () => {
    if (!next) return;
    setLoadingMore(true);
    try {
      const page = await source.fetch(next);
      const older = page.lines.filter((l) => !known.current.has(l.key));
      older.forEach((l) => known.current.add(l.key));
      setLines((prev) => [...(prev ?? []), ...older]);
      setNext(page.next);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoadingMore(false);
    }
  };

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return needle ? (lines ?? []).filter((l) => lineToText(l).toLowerCase().includes(needle)) : (lines ?? []);
  }, [lines, q]);
  const plain = useMemo(() => shown.map(lineToText).join('\n'), [shown]);

  return (
    <div className="space-y-3 rounded-2xl border bg-card p-4">
      <div className="flex flex-wrap items-center gap-3">
        <label className="relative block min-w-56 flex-1">
          <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t('admin.logs.filter')} className="h-9 w-full rounded-lg border bg-background pr-3 pl-9 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring" />
        </label>
        <label className="flex h-9 items-center gap-2 text-sm">
          <input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} />
          {t('admin.logs.live')}
        </label>
        <Button size="sm" variant="tertiary" leadingIcon={RefreshCw} onClick={() => void refresh()}>{t('admin.logs.refresh')}</Button>
        <CopyButton text={plain} label={t('admin.logs.copy')} />
        <button
          type="button"
          title={t('admin.logs.download')}
          aria-label={t('admin.logs.download')}
          disabled={shown.length === 0}
          onClick={() => download(`cerebro-${source.id}.log`, plain + '\n')}
          className="inline-flex h-7 min-w-7 items-center justify-center rounded-md border hover:bg-muted disabled:opacity-50"
        >
          <Download className="size-4" />
        </button>
      </div>

      {error && <p role="alert" className="text-sm text-destructive-text">{error}</p>}

      <div className="max-h-[65vh] overflow-auto rounded-xl border bg-background p-3 font-mono text-[12px] leading-relaxed" role="log" aria-label={t(`admin.logs.sources.${source.id}`)}>
        {lines === null ? (
          <p className="text-muted-foreground">…</p>
        ) : shown.length === 0 ? (
          <p className="text-muted-foreground">{lines.length === 0 ? t('admin.logs.empty') : t('admin.logs.noMatch')}</p>
        ) : (
          shown.map((l) => (
            <div key={l.key} className="whitespace-pre">
              <span className="text-muted-foreground">{l.at}</span> {l.text}
            </div>
          ))
        )}
        {next && !q && (
          <div className="pt-3">
            <Button size="sm" variant="tertiary" disabled={loadingMore} onClick={() => void more()}>{loadingMore ? '…' : t('admin.logs.older')}</Button>
          </div>
        )}
      </div>
      <p className="text-xs text-muted-foreground">{t('admin.logs.count', { count: shown.length })} · {t('admin.logs.hint')}</p>
    </div>
  );
}

export function Logs() {
  const { t } = useTranslation();
  const { canSee } = useAuth();
  const sources = LOG_SOURCES.filter((s) => canSee(s.module));
  const [active, setActive] = useState(sources[0]?.id ?? '');
  const source = sources.find((s) => s.id === active) ?? sources[0];

  return (
    <>
      <PageHeader title={t('admin.logs.title')} description={t('admin.logs.description')} />
      <div role="tablist" className="inline-flex gap-1 rounded-xl border p-1">
        {sources.map((s) => (
          <button
            key={s.id}
            type="button"
            role="tab"
            aria-selected={source?.id === s.id}
            onClick={() => setActive(s.id)}
            className={`inline-flex items-center rounded-lg px-4 py-2 text-sm font-medium transition-colors ${source?.id === s.id ? 'bg-foreground text-background' : 'text-muted-foreground hover:text-foreground'}`}
          >
            {t(`admin.logs.sources.${s.id}`)}
          </button>
        ))}
      </div>
      {source && <LogView key={source.id} source={source} />}
    </>
  );
}
