import { useEffect, useState } from 'react';
import { Badge, Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@alquimias/ixchel';
import { useTranslation } from 'react-i18next';
import { getMemory, getRelated, type Memory, type Related } from '../lib/memories';
import { typeVariant, statusVariant, useDateTime } from './memoryUi';

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-sm">{children}</dd>
    </div>
  );
}

/** Right-hand panel with one memory, its metadata and its 1-hop neighbors. */
export function MemoryDetail({ id, onOpen, onClose }: { id: string | null; onOpen: (id: string) => void; onClose: () => void }) {
  const { t } = useTranslation();
  const fmt = useDateTime();
  const [memory, setMemory] = useState<Memory | null>(null);
  const [related, setRelated] = useState<Related[]>([]);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    setMemory(null);
    setRelated([]);
    setError(false);
    getMemory(id).then(
      (m) => !cancelled && setMemory(m),
      () => !cancelled && setError(true),
    );
    getRelated(id).then(
      (r) => !cancelled && setRelated(r),
      () => undefined, // neighbors are a nice-to-have: the memory itself still renders
    );
    return () => {
      cancelled = true;
    };
  }, [id]);

  return (
    <Sheet open={id !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent side="right" className="w-full overflow-y-auto p-6 sm:max-w-xl">
        <SheetHeader className="p-0 pr-8">
          <SheetTitle className="text-lg">{memory?.title || (error ? t('memory.detail.loadError') : '…')}</SheetTitle>
          <SheetDescription className="flex flex-wrap items-center gap-1.5">
            {memory && (
              <>
                <Badge variant="outline">{memory.context}</Badge>
                <Badge variant={typeVariant(memory.type)}>{t(`memory.types.${memory.type}`, { defaultValue: memory.type })}</Badge>
                {memory.status !== 'active' && <Badge variant={statusVariant(memory.status)}>{t(`memory.statuses.${memory.status}`, { defaultValue: memory.status })}</Badge>}
              </>
            )}
          </SheetDescription>
        </SheetHeader>

        {memory && (
          <div className="space-y-6">
            <section className="space-y-1.5">
              <h3 className="text-xs font-bold tracking-wider text-muted-foreground uppercase">{t('memory.detail.content')}</h3>
              <p className="text-sm leading-relaxed whitespace-pre-wrap">{memory.content}</p>
            </section>

            <section className="space-y-2">
              <h3 className="text-xs font-bold tracking-wider text-muted-foreground uppercase">{t('memory.detail.details')}</h3>
              <dl className="grid grid-cols-2 gap-x-4 gap-y-3">
                <Field label={t('memory.importance')}>{memory.importance.toFixed(2)}</Field>
                <Field label={t('memory.detail.confidence')}>{memory.confidence.toFixed(2)}</Field>
                <Field label={t('memory.created')}>{fmt(memory.created_at)}</Field>
                <Field label={t('memory.updated')}>{fmt(memory.updated_at)}</Field>
                {memory.occurred_at && <Field label={t('memory.detail.occurred')}>{fmt(memory.occurred_at)}</Field>}
                {memory.source && <Field label={t('memory.detail.source')}>{memory.source}</Field>}
              </dl>
              {memory.superseded_by && (
                <button type="button" onClick={() => onOpen(memory.superseded_by!)} className="text-sm text-brand-soft hover:underline">
                  {t('memory.detail.supersededBy')} →
                </button>
              )}
            </section>

            <section className="space-y-2">
              <h3 className="text-xs font-bold tracking-wider text-muted-foreground uppercase">{t('memory.detail.related')}</h3>
              {related.length === 0 ? (
                <p className="text-sm text-muted-foreground">{t('memory.detail.noRelated')}</p>
              ) : (
                <ul className="space-y-2">
                  {related.map((r) => (
                    <li key={`${r.relation}-${r.direction}-${r.memory.id}`}>
                      <button type="button" onClick={() => onOpen(r.memory.id)} className="w-full rounded-lg border p-3 text-left transition-colors hover:bg-muted">
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
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
