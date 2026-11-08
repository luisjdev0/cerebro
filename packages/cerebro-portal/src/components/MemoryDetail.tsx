import { useCallback, useEffect, useRef, useState, type PointerEvent as RPointerEvent } from 'react';
import { Badge, Button, ConfirmDialog, Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@alquimias/ixchel';
import { Calendar, GitBranch, Gauge, Pencil, ShieldCheck, Trash2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { forgetMemory, getMemory, getRelated, updateMemory, type Memory, type Related } from '../lib/memories';
import { INPUT, useErrorMessage } from './formUi';
import { statusVariant, typeVariant, useDateTime } from './memoryUi';

const WIDTH_KEY = 'cerebro.memory.panelWidth';
const MIN_W = 380;
const DEFAULT_W = 560;

const maxWidth = () => Math.min(1100, Math.floor(window.innerWidth * 0.9));
const clamp = (w: number) => Math.max(MIN_W, Math.min(maxWidth(), w));

function loadWidth(): number {
  try {
    const n = Number(localStorage.getItem(WIDTH_KEY));
    return Number.isFinite(n) && n > 0 ? clamp(n) : DEFAULT_W;
  } catch {
    return DEFAULT_W;
  }
}

/** Panel width that the user resizes by dragging its left edge; remembered across visits. */
function useResizableWidth() {
  const [width, setWidth] = useState(loadWidth);
  const drag = useRef<{ startX: number; startW: number } | null>(null);

  const onPointerDown = (e: RPointerEvent<HTMLDivElement>) => {
    drag.current = { startX: e.clientX, startW: width };
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: RPointerEvent<HTMLDivElement>) => {
    if (drag.current) setWidth(clamp(drag.current.startW + (drag.current.startX - e.clientX)));
  };
  const onPointerUp = () => {
    if (!drag.current) return;
    drag.current = null;
    try {
      localStorage.setItem(WIDTH_KEY, String(width));
    } catch {
      /* the width just will not be remembered */
    }
  };
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    const w = clamp(width + (e.key === 'ArrowLeft' ? 24 : -24));
    setWidth(w);
    try {
      localStorage.setItem(WIDTH_KEY, String(w));
    } catch {
      /* ignore */
    }
  };
  return { width, handleProps: { onPointerDown, onPointerMove, onPointerUp, onPointerCancel: onPointerUp, onKeyDown } };
}

function Stat({ icon: Icon, label, children }: { icon: typeof Gauge; label: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border bg-muted/30 p-3">
      <dt className="flex items-center gap-1.5 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
        <Icon className="size-3.5" />
        {label}
      </dt>
      <dd className="mt-1 text-sm font-semibold tabular-nums">{children}</dd>
    </div>
  );
}

function Meter({ value }: { value: number }) {
  return (
    <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-muted">
      <div className="h-full rounded-full bg-brand-soft" style={{ width: `${Math.round(value * 100)}%` }} />
    </div>
  );
}

/** Right-hand panel: one memory, its metadata and neighbors; editable and resizable. */
export function MemoryDetail({ id, onOpen, onClose, onChanged }: {
  id: string | null;
  onOpen: (id: string) => void;
  onClose: () => void;
  /** Called after an edit or forget so the list behind the panel can refresh. */
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const fmt = useDateTime();
  const errMsg = useErrorMessage();
  const { width, handleProps } = useResizableWidth();
  const [memory, setMemory] = useState<Memory | null>(null);
  const [related, setRelated] = useState<Related[]>([]);
  const [error, setError] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string>();
  const [confirmForget, setConfirmForget] = useState(false);

  const load = useCallback(
    (target: string) => {
      let cancelled = false;
      setMemory(null);
      setRelated([]);
      setError(false);
      setEditing(false);
      setActionError(undefined);
      getMemory(target).then(
        (m) => !cancelled && setMemory(m),
        () => !cancelled && setError(true),
      );
      getRelated(target).then(
        (r) => !cancelled && setRelated(r),
        () => undefined, // neighbors are a nice-to-have: the memory itself still renders
      );
      return () => {
        cancelled = true;
      };
    },
    [],
  );

  useEffect(() => (id ? load(id) : undefined), [id, load]);

  const save = async () => {
    if (!memory) return;
    setBusy(true);
    setActionError(undefined);
    try {
      const next = await updateMemory(memory.id, draft.trim());
      onChanged();
      onOpen(next.id); // the edit is a new version: follow it
    } catch (e) {
      setActionError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const forget = async () => {
    if (!memory) return;
    setBusy(true);
    try {
      await forgetMemory(memory.id);
      setConfirmForget(false);
      onChanged();
      onClose();
    } catch (e) {
      setConfirmForget(false);
      setActionError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const editable = memory?.status === 'active';

  return (
    <Sheet open={id !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent side="right" className="w-full gap-0 overflow-hidden p-0 data-[side=right]:sm:max-w-none" style={{ width: `min(${width}px, 100vw)` }}>
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label={t('memory.detail.resize')}
          title={t('memory.detail.resize')}
          tabIndex={0}
          {...handleProps}
          className="group absolute inset-y-0 left-0 z-10 hidden w-2 cursor-col-resize touch-none items-center justify-center sm:flex"
        >
          <span className="h-12 w-1 rounded-full bg-border transition-colors group-hover:bg-brand-soft group-focus-visible:bg-brand-soft" />
        </div>

        <SheetHeader className="space-y-2 border-b bg-muted/30 p-6 pr-12">
          <div className="flex flex-wrap items-center gap-1.5">
            {memory && (
              <>
                <Badge variant="outline">{memory.context}</Badge>
                <Badge variant={typeVariant(memory.type)}>{t(`memory.types.${memory.type}`, { defaultValue: memory.type })}</Badge>
                {memory.status !== 'active' && <Badge variant={statusVariant(memory.status)}>{t(`memory.statuses.${memory.status}`, { defaultValue: memory.status })}</Badge>}
              </>
            )}
          </div>
          <SheetTitle className="text-xl leading-snug">{memory?.title || (error ? t('memory.detail.loadError') : '…')}</SheetTitle>
          <SheetDescription className="sr-only">{memory?.title}</SheetDescription>
        </SheetHeader>

        <div className="flex-1 space-y-7 overflow-y-auto p-6">
          {memory && (
            <>
              <section className="space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <h3 className="text-xs font-bold tracking-wider text-muted-foreground uppercase">{t('memory.detail.content')}</h3>
                  {editable && !editing && (
                    <Button size="sm" variant="ghost" leadingIcon={Pencil} onClick={() => { setDraft(memory.content); setEditing(true); setActionError(undefined); }}>
                      {t('memory.actions.edit')}
                    </Button>
                  )}
                </div>
                {editing ? (
                  <div className="space-y-2">
                    <textarea autoFocus rows={10} className={`${INPUT} leading-relaxed`} value={draft} onChange={(e) => setDraft(e.target.value)} />
                    <p className="text-xs text-muted-foreground">{t('memory.editNote')}</p>
                    <div className="flex justify-end gap-2">
                      <Button size="sm" variant="ghost" disabled={busy} onClick={() => setEditing(false)}>{t('memory.actions.cancel')}</Button>
                      <Button size="sm" variant="primary" disabled={busy || !draft.trim() || draft.trim() === memory.content} onClick={() => void save()}>
                        {busy ? t('memory.actions.saving') : t('memory.actions.save')}
                      </Button>
                    </div>
                  </div>
                ) : (
                  <p className="rounded-xl border bg-card p-4 text-sm leading-relaxed whitespace-pre-wrap">{memory.content}</p>
                )}
                {actionError && <p role="alert" className="text-sm text-destructive-text">{actionError}</p>}
              </section>

              <section className="space-y-3">
                <h3 className="text-xs font-bold tracking-wider text-muted-foreground uppercase">{t('memory.detail.details')}</h3>
                <dl className="grid grid-cols-2 gap-3">
                  <Stat icon={Gauge} label={t('memory.importance')}>
                    {memory.importance.toFixed(2)}
                    <Meter value={memory.importance} />
                  </Stat>
                  <Stat icon={ShieldCheck} label={t('memory.detail.confidence')}>
                    {memory.confidence.toFixed(2)}
                    <Meter value={memory.confidence} />
                  </Stat>
                  <Stat icon={Calendar} label={t('memory.created')}>{fmt(memory.created_at)}</Stat>
                  <Stat icon={Calendar} label={t('memory.updated')}>{fmt(memory.updated_at)}</Stat>
                  {memory.occurred_at && <Stat icon={Calendar} label={t('memory.detail.occurred')}>{fmt(memory.occurred_at)}</Stat>}
                  {memory.source && <Stat icon={GitBranch} label={t('memory.detail.source')}>{memory.source}</Stat>}
                </dl>
                {memory.superseded_by && (
                  <button type="button" onClick={() => onOpen(memory.superseded_by!)} className="text-sm text-brand-soft hover:underline">
                    {t('memory.detail.supersededBy')} →
                  </button>
                )}
              </section>

              <section className="space-y-3">
                <h3 className="text-xs font-bold tracking-wider text-muted-foreground uppercase">{t('memory.detail.related')}</h3>
                {related.length === 0 ? (
                  <p className="text-sm text-muted-foreground">{t('memory.detail.noRelated')}</p>
                ) : (
                  <ul className="space-y-2">
                    {related.map((r) => (
                      <li key={`${r.relation}-${r.direction}-${r.memory.id}`}>
                        <button type="button" onClick={() => onOpen(r.memory.id)} className="w-full rounded-xl border p-3 text-left transition-colors hover:bg-muted">
                          <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                            <Badge variant="ghost" className="px-0">{r.relation}</Badge>
                            <span>· {t(`memory.detail.${r.direction === 'incoming' ? 'incoming' : 'outgoing'}`)}</span>
                          </span>
                          <span className="mt-0.5 block text-sm font-medium">{r.memory.title}</span>
                          {r.note && <span className="mt-0.5 block text-xs text-muted-foreground">{r.note}</span>}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </>
          )}
        </div>

        {memory && editable && (
          <div className="flex justify-end border-t p-3">
            <Button size="sm" variant="ghost" className="text-destructive-text" leadingIcon={Trash2} onClick={() => setConfirmForget(true)}>
              {t('memory.actions.forget')}
            </Button>
          </div>
        )}

        <ConfirmDialog
          open={confirmForget}
          onOpenChange={setConfirmForget}
          title={t('memory.forgetTitle')}
          description={t('memory.forgetDesc')}
          confirmLabel={t('memory.forgetConfirm')}
          cancelLabel={t('memory.actions.cancel')}
          isPending={busy}
          onConfirm={() => void forget()}
        />
      </SheetContent>
    </Sheet>
  );
}
