import { useEffect, useState } from 'react';
import { Button, ConfirmDialog, SheetDescription, SheetHeader, SheetTitle } from '@alquimias/ixchel';
import { Pencil, Plus, Trash2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import {
  countCategoryFlows,
  createFlowCategory,
  deleteFlowCategory,
  updateFlowCategory,
  type FlowCategory,
} from '../lib/flows';
import { slugify } from '../lib/memories';
import { Field, INPUT, useErrorMessage } from './formUi';
import { ResizableSheet } from './ResizableSheet';

function CategoryForm({ initial, onSubmit, onCancel, busy, error }: {
  initial?: FlowCategory;
  onSubmit: (v: { name: string; code: string; description: string }) => void;
  onCancel: () => void;
  busy: boolean;
  error?: string;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState(initial?.name ?? '');
  const [code, setCode] = useState(initial?.code ?? '');
  const [description, setDescription] = useState(initial?.description ?? '');

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit({ name: name.trim(), code: code.trim().toUpperCase(), description: description.trim() });
      }}
      className="space-y-3 rounded-xl border bg-card p-4"
    >
      <Field label={t('flows.cat.name')}>
        <input autoFocus required className={INPUT} value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      {initial ? (
        <p className="text-xs text-muted-foreground">{t('flows.cat.fixedHint', { slug: initial.slug, code: initial.code })}</p>
      ) : (
        <Field label={t('flows.cat.code')} hint={t('flows.cat.codeHint')}>
          <input required pattern="[A-Za-z0-9]+" maxLength={10} className={`${INPUT} font-mono uppercase`} value={code} onChange={(e) => setCode(e.target.value)} />
        </Field>
      )}
      <Field label={t('flows.cat.description')}>
        <textarea rows={2} className={INPUT} value={description} onChange={(e) => setDescription(e.target.value)} />
      </Field>
      {error && <p role="alert" className="text-sm text-destructive-text">{error}</p>}
      <div className="flex justify-end gap-2">
        <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={onCancel}>{t('flows.cat.cancel')}</Button>
        <Button type="submit" size="sm" variant="primary" arrow={false} disabled={busy || !name.trim() || (!initial && !code.trim())}>
          {busy ? t('flows.cat.saving') : initial ? t('flows.cat.save') : t('flows.cat.create')}
        </Button>
      </div>
    </form>
  );
}

/** Side panel to list, create, edit and delete flow categories. */
export function FlowCategoriesPanel({ open, categories, onClose, onChanged }: {
  open: boolean;
  categories: FlowCategory[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const errMsg = useErrorMessage();
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [toDelete, setToDelete] = useState<{ category: FlowCategory; count: number } | null>(null);
  const [counts, setCounts] = useState<Record<string, number>>({});

  // Flow count per category, shown on each row; refreshed whenever the list changes.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    void Promise.all(categories.map((c) => countCategoryFlows(c.slug).then((n) => [c.slug, n] as const, () => [c.slug, -1] as const))).then((rows) => {
      if (!cancelled) setCounts(Object.fromEntries(rows));
    });
    return () => {
      cancelled = true;
    };
  }, [open, categories]);

  const run = async (fn: () => Promise<unknown>, done: () => void) => {
    setBusy(true);
    setError(undefined);
    try {
      await fn();
      done();
      onChanged();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };
  const askDelete = async (c: FlowCategory) => {
    setError(undefined);
    try {
      setToDelete({ category: c, count: await countCategoryFlows(c.slug) });
    } catch (e) {
      setError(errMsg(e));
    }
  };
  const doDelete = async () => {
    if (!toDelete) return;
    const { category, count } = toDelete;
    setToDelete(null);
    await run(() => deleteFlowCategory(category.slug, count > 0), () => undefined);
  };
  const close = () => {
    setCreating(false);
    setEditing(null);
    setError(undefined);
    onClose();
  };

  return (
    <ResizableSheet open={open} onClose={close}>
      <SheetHeader className="space-y-1.5 border-b bg-muted/30 p-6 pr-12">
        <SheetTitle className="text-xl leading-snug">{t('flows.cat.title')}</SheetTitle>
        <SheetDescription>{t('flows.cat.desc')}</SheetDescription>
      </SheetHeader>
      <div className="flex-1 space-y-3 overflow-y-auto p-6">
        {creating ? (
          <CategoryForm
            busy={busy}
            error={error}
            onCancel={() => setCreating(false)}
            onSubmit={(v) => void run(() => createFlowCategory({ slug: slugify(v.name), code: v.code, name: v.name, description: v.description || undefined }), () => setCreating(false))}
          />
        ) : (
          <Button variant="tertiary" leadingIcon={Plus} onClick={() => { setCreating(true); setError(undefined); }}>{t('flows.cat.new')}</Button>
        )}
        {!creating && error && <p role="alert" className="text-sm text-destructive-text">{error}</p>}
        <ul className="space-y-2">
          {categories.length === 0 && <li className="py-6 text-center text-sm text-muted-foreground">{t('flows.cat.none')}</li>}
          {categories.map((c) => (
            <li key={c.slug}>
              {editing === c.slug ? (
                <CategoryForm
                  initial={c}
                  busy={busy}
                  error={error}
                  onCancel={() => setEditing(null)}
                  onSubmit={(v) => void run(() => updateFlowCategory(c.slug, { name: v.name, description: v.description }), () => setEditing(null))}
                />
              ) : (
                <div className="flex items-start justify-between gap-3 rounded-xl border p-3">
                  <div className="min-w-0">
                    <p className="truncate font-medium">
                      {c.name} <span className="font-mono text-xs font-normal text-muted-foreground">{c.code} · {c.slug}</span>
                    </p>
                    {c.description && <p className="text-xs text-muted-foreground">{c.description}</p>}
                    {counts[c.slug] !== undefined && counts[c.slug] >= 0 && (
                      <p className="mt-1 text-xs text-muted-foreground">{t('flows.cat.flowCount', { count: counts[c.slug] })}</p>
                    )}
                  </div>
                  <div className="flex shrink-0 gap-1">
                    <Button size="sm" variant="ghost" leadingIcon={Pencil} onClick={() => { setEditing(c.slug); setError(undefined); }}>{t('flows.cat.edit')}</Button>
                    <Button size="sm" variant="ghost" className="text-destructive-text" leadingIcon={Trash2} onClick={() => void askDelete(c)}>{t('flows.cat.delete')}</Button>
                  </div>
                </div>
              )}
            </li>
          ))}
        </ul>
      </div>
      <ConfirmDialog
        open={toDelete !== null}
        onOpenChange={(o) => !o && setToDelete(null)}
        title={t('flows.cat.deleteTitle', { name: toDelete?.category.name })}
        description={toDelete && toDelete.count > 0 ? t('flows.cat.deleteWith', { count: toDelete.count }) : t('flows.cat.deleteEmpty')}
        confirmLabel={t('flows.cat.delete')}
        cancelLabel={t('flows.cat.cancel')}
        isPending={busy}
        onConfirm={() => void doDelete()}
      />
    </ResizableSheet>
  );
}
