import { Badge, SheetDescription, SheetHeader, SheetTitle } from '@alquimias/ixchel';
import { useTranslation } from 'react-i18next';
import { ApiError } from '../lib/api';
import { isLastAdminError, type AccessLevel, type ModuleId } from '../lib/admin';
import { useErrorMessage } from './formUi';

export function PanelHeader({ title, description }: { title: string; description?: string }) {
  return (
    <SheetHeader className="space-y-1.5 border-b bg-muted/30 p-6 pr-12">
      <SheetTitle className="text-xl leading-snug">{title}</SheetTitle>
      {description && <SheetDescription>{description}</SheetDescription>}
    </SheetHeader>
  );
}

export function LevelBadge({ level }: { level: AccessLevel | string | null }) {
  const { t } = useTranslation();
  if (!level) return null;
  return <Badge variant={level === 'admin' ? 'warning' : level === 'owner' ? 'info' : 'outline'}>{t(`admin.level.${level}`, { defaultValue: level })}</Badge>;
}

/** What a token or group reaches: the enabled modules, each marked when it is limited to a list. */
export function ModuleChips({ modules, restricted, all }: { modules: ModuleId[] | null; restricted: Partial<Record<ModuleId, boolean>>; all?: string }) {
  const { t } = useTranslation();
  if (modules === null) return <span className="text-xs text-muted-foreground">{all ?? t('admin.inherits')}</span>;
  if (modules.length === 0) return <span className="text-xs text-muted-foreground">—</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {modules.map((m) => (
        <Badge key={m} variant="outline">
          {t(`admin.modules.${m}`)}
          {restricted[m] ? ' *' : ''}
        </Badge>
      ))}
    </span>
  );
}

/** Like the shared error mapper, but the 409 "last admin" gets its own wording. */
export function useAdminError() {
  const { t } = useTranslation();
  const base = useErrorMessage();
  return (e: unknown) => {
    if (isLastAdminError(e)) return t('admin.errors.lastAdmin');
    if (e instanceof ApiError && e.status === 409 && e.message) return e.message;
    return base(e);
  };
}
