import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '../lib/api';

export const INPUT = 'w-full rounded-lg border bg-background px-3 py-2 text-sm outline-none transition-shadow placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60';

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block space-y-1.5">
      <span className="text-xs font-semibold">{label}</span>
      {children}
      {hint && <span className="block text-[11px] text-muted-foreground">{hint}</span>}
    </label>
  );
}

/** Maps an API failure to a message the user can act on (403 = no permission, 409 = taken). */
export function useErrorMessage() {
  const { t } = useTranslation();
  return (e: unknown): string => {
    if (e instanceof ApiError) {
      if (e.status === 403) return t('memory.errors.forbidden');
      if (e.status === 409) return t('memory.errors.exists');
      if (e.status === 0) return e.message;
      return e.message || t('memory.errors.generic');
    }
    return t('memory.errors.generic');
  };
}
