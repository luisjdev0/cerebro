import { useCallback, useEffect, useState } from 'react';
import { Badge, SheetDescription, SheetHeader, SheetTitle } from '@alquimias/ixchel';
import { useTranslation } from 'react-i18next';
import { getRun, getRunEvents, type RunEvent, type RunStep, type RunSummary } from '../lib/flows';
import { RunStatusBadge } from './flowUi';
import { useDateTime } from './memoryUi';
import { ResizableSheet } from './ResizableSheet';

function payloadText(p: unknown): string {
  if (p && typeof p === 'object') {
    const o = p as Record<string, unknown>;
    if (typeof o.reason === 'string') return o.reason;
    if (typeof o.decision === 'string') return o.decision;
    return Object.keys(o).length ? JSON.stringify(o) : '';
  }
  return p == null ? '' : String(p);
}

function StepCard({ step }: { step: RunStep }) {
  const { t } = useTranslation();
  return (
    <div className="space-y-3 rounded-xl border bg-muted/30 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-sm font-semibold">{step.id}</span>
        <Badge variant="outline">{step.type}</Badge>
      </div>
      {step.description && <p className="text-sm leading-relaxed">{step.description}</p>}
      {step.tools_required && step.tools_required.length > 0 && (
        <p className="text-xs text-muted-foreground">{t('flows.run.tools')}: <span className="font-mono">{step.tools_required.join(', ')}</span></p>
      )}
      {step.branches && (
        <p className="text-xs text-muted-foreground">{t('flows.run.branches')}: <span className="font-mono">{Object.entries(step.branches).map(([k, v]) => `${k} → ${v}`).join(' · ')}</span></p>
      )}
    </div>
  );
}

/** Right-hand panel: where one run is, its audit trail, and the human actions (approve / reject / abort). */
/** Read-only view of a run. Approving, rejecting or aborting is the agent's job (MCP/CLI), not the portal's. */
export function RunDetail({ run, onClose }: { run: RunSummary | null; onClose: () => void }) {
  const { t } = useTranslation();
  const fmt = useDateTime();
  const [step, setStep] = useState<RunStep | RunStep[] | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [error, setError] = useState(false);

  const id = run?.run_id ?? null;
  const position = `${run?.status}|${run?.current_step_id}|${run?.awaiting_approval}|${run?.expired}`;
  const load = useCallback((runId: string, live: boolean) => {
    let cancelled = false;
    setError(false);
    getRunEvents(runId).then((e) => !cancelled && setEvents(e), () => !cancelled && setError(true));
    if (live) getRun(runId).then((s) => !cancelled && setStep(s.step), () => !cancelled && setStep(null));
    else setStep(null);
    return () => {
      cancelled = true;
    };
  }, []);
  useEffect(() => {
    if (!id || !run) return undefined;
    return load(id, run.status === 'in_progress' && !run.expired);
  }, [id, position]); // eslint-disable-line react-hooks/exhaustive-deps
  const live = run?.status === 'in_progress' && !run.expired;
  const steps = Array.isArray(step) ? step : step ? [step] : [];

  return (
    <ResizableSheet open={run !== null} onClose={onClose}>
      <SheetHeader className="space-y-2 border-b bg-muted/30 p-6 pr-12">
        <div className="flex flex-wrap items-center gap-1.5">
          {run && (
            <>
              <Badge variant="outline">{run.category}</Badge>
              <Badge variant="secondary" className="font-mono">{run.flow_code}</Badge>
              <RunStatusBadge run={run} />
            </>
          )}
        </div>
        <SheetTitle className="text-xl leading-snug">{run?.flow_name ?? '…'}</SheetTitle>
        <SheetDescription className="font-mono text-xs">{run?.run_id}</SheetDescription>
      </SheetHeader>

      <div className="flex-1 space-y-7 overflow-y-auto p-6">
        {run && (
          <>
            {run.expired && <p className="rounded-lg border bg-muted/40 p-3 text-xs text-muted-foreground">{t('flows.run.expiredNote')}</p>}

            {live && steps.length > 0 && (
              <section className="space-y-3">
                <h3 className="text-xs font-bold tracking-wider text-muted-foreground uppercase">{t('flows.run.currentStep')}</h3>
                {steps.map((s) => (
                  <StepCard key={s.id} step={s} />
                ))}
              </section>
            )}

            {live && run.awaiting_approval && !Array.isArray(step) && step?.checkpoint && (
              <section className="space-y-2 rounded-xl border border-amber-500/40 bg-amber-500/5 p-4">
                <h3 className="text-xs font-bold tracking-wider text-muted-foreground uppercase">{t('flows.run.approval')}</h3>
                <p className="text-sm font-medium">{step.checkpoint.prompt}</p>
                <p className="text-xs text-muted-foreground">{t('flows.run.approvalNote', { step: step.checkpoint.on_reject })}</p>
              </section>
            )}

            <section className="space-y-3">
              <h3 className="text-xs font-bold tracking-wider text-muted-foreground uppercase">{t('flows.run.timeline')}</h3>
              {error ? (
                <p className="text-sm text-muted-foreground">{t('flows.run.loadError')}</p>
              ) : events.length === 0 ? (
                <div className="h-8 animate-pulse rounded bg-muted" />
              ) : (
                <ol className="space-y-0">
                  {events.map((e, i) => {
                    const extra = payloadText(e.payload);
                    return (
                      <li key={i} className="relative border-l pb-4 pl-4 last:pb-0">
                        <span className="absolute top-1.5 -left-[4.5px] size-2 rounded-full bg-brand-soft" />
                        <p className="text-sm font-medium">
                          {t(`flows.events.${e.event_type}`, { defaultValue: e.event_type })}
                          {e.step_id && <span className="ml-2 font-mono text-xs font-normal text-muted-foreground">{e.step_id}</span>}
                        </p>
                        {extra && <p className="text-xs break-words text-muted-foreground">{extra}</p>}
                        <p className="text-[11px] text-muted-foreground tabular-nums">{fmt(e.created_at)}</p>
                      </li>
                    );
                  })}
                </ol>
              )}
            </section>
          </>
        )}
      </div>

    </ResizableSheet>
  );
}
