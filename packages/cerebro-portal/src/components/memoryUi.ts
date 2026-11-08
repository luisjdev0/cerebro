import { useLocale } from '../lib/i18n';

type BadgeVariant = 'default' | 'secondary' | 'success' | 'warning' | 'info' | 'outline';

export const typeVariant = (type: string): BadgeVariant =>
  ({ semantic: 'info', episodic: 'secondary', procedural: 'success', decision: 'warning' } as Record<string, BadgeVariant>)[type] ?? 'outline';

export const statusVariant = (status: string): BadgeVariant => (status === 'archived' ? 'outline' : 'warning');

/** Formats an ISO timestamp as a short date + time in the current language. */
export function useDateTime() {
  const { lang } = useLocale();
  const f = new Intl.DateTimeFormat(lang, { dateStyle: 'medium', timeStyle: 'short' });
  return (iso: string) => f.format(new Date(iso));
}
export function useShortDate() {
  const { lang } = useLocale();
  const f = new Intl.DateTimeFormat(lang, { dateStyle: 'medium' });
  return (iso: string) => f.format(new Date(iso));
}
