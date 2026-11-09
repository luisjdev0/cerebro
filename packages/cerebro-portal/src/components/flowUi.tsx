import { useEffect, useState } from 'react';
import { Badge } from '@alquimias/ixchel';
import { useTranslation } from 'react-i18next';
import type { RunSummary } from '../lib/flows';

export const SELECT = 'h-9 w-full rounded-lg border bg-background px-2.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring';
export const PAGE_SIZES = [10, 20, 50, 100];

export function loadPageSize(key: string): number {
  try {
    const n = Number(localStorage.getItem(key));
    return PAGE_SIZES.includes(n) ? n : 10;
  } catch {
    return 10;
  }
}

export function useDebounced<T>(value: T, ms = 350): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const h = setTimeout(() => setV(value), ms);
    return () => clearTimeout(h);
  }, [value, ms]);
  return v;
}

export function FilterField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block min-w-0 space-y-1">
      <span className="text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">{label}</span>
      {children}
    </label>
  );
}

/** Status of a run as one badge: awaiting approval and expired are states of an in-progress run. */
export function RunStatusBadge({ run }: { run: Pick<RunSummary, 'status' | 'awaiting_approval' | 'expired'> }) {
  const { t } = useTranslation();
  if (run.status === 'in_progress' && run.expired) return <Badge variant="outline">{t('flows.runStatus.expired')}</Badge>;
  if (run.status === 'in_progress' && run.awaiting_approval) return <Badge variant="warning">{t('flows.runStatus.awaiting')}</Badge>;
  const variant = run.status === 'completed' ? 'success' : run.status === 'aborted' ? 'outline' : 'info';
  return <Badge variant={variant}>{t(`flows.runStatus.${run.status}`, { defaultValue: run.status })}</Badge>;
}
