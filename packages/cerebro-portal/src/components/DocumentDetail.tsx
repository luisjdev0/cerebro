import { useCallback, useEffect, useState } from 'react';
import { Badge, Button, ConfirmDialog, SheetDescription, SheetHeader, SheetTitle } from '@alquimias/ixchel';
import { Archive, ArchiveRestore, Pencil, Trash2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import {
  archiveDocument,
  deleteDocument,
  getDocument,
  getVersions,
  replaceDocument,
  unarchiveDocument,
  type Category,
  type Doc,
  type DocVersion,
} from '../lib/documents';
import { Field, INPUT, useErrorMessage } from './formUi';
import { Markdown } from './Markdown';
import { MarkdownEditor } from './MarkdownEditor';
import { useDateTime } from './memoryUi';
import { ResizableSheet } from './ResizableSheet';

export type DocRef = { category: string; slug: string };

/** Right-hand panel: one document rendered, its history, and editing/archiving. */
export function DocumentDetail({ docRef, categories, onOpen, onClose, onChanged }: {
  docRef: DocRef | null;
  categories: Category[];
  onOpen: (r: DocRef) => void;
  onClose: () => void;
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const fmt = useDateTime();
  const errMsg = useErrorMessage();
  const [doc, setDoc] = useState<Doc | null>(null);
  const [versions, setVersions] = useState<DocVersion[]>([]);
  const [shown, setShown] = useState<number | null>(null);
  const [error, setError] = useState(false);
  const [editing, setEditing] = useState(false);
  const [dTitle, setDTitle] = useState('');
  const [dContent, setDContent] = useState('');
  const [dCategory, setDCategory] = useState('');
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string>();
  const [confirm, setConfirm] = useState<'archive' | 'delete' | null>(null);

  const key = docRef ? `${docRef.category}/${docRef.slug}` : null;
  const load = useCallback((r: DocRef) => {
    let cancelled = false;
    setDoc(null);
    setVersions([]);
    setShown(null);
    setError(false);
    setEditing(false);
    setActionError(undefined);
    getDocument(r.category, r.slug).then(
      (d) => {
        if (cancelled) return;
        setDoc(d);
        getVersions(d.id).then((v) => !cancelled && setVersions(v), () => undefined); // history is a nice-to-have
      },
      () => !cancelled && setError(true),
    );
    return () => {
      cancelled = true;
    };
  }, []);
  useEffect(() => (docRef ? load(docRef) : undefined), [key]); // eslint-disable-line react-hooks/exhaustive-deps

  const startEdit = () => {
    if (!doc) return;
    setDTitle(doc.title);
    setDContent(doc.content);
    setDCategory(doc.category);
    setEditing(true);
    setActionError(undefined);
  };

  const save = async () => {
    if (!doc) return;
    setBusy(true);
    setActionError(undefined);
    try {
      const next = await replaceDocument(doc.id, { title: dTitle.trim(), content: dContent, category: dCategory });
      onChanged();
      if (next.category === doc.category && next.slug === doc.slug) {
        setDoc(next);
        setEditing(false);
        getVersions(next.id).then(setVersions, () => undefined);
      } else {
        onOpen({ category: next.category, slug: next.slug }); // it moved: follow it
      }
    } catch (e) {
      setActionError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const act = async (fn: () => Promise<unknown>, after: () => void) => {
    setBusy(true);
    setActionError(undefined);
    try {
      await fn();
      setConfirm(null);
      onChanged();
      after();
    } catch (e) {
      setConfirm(null);
      setActionError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const archived = doc?.status === 'archived';
  const unchanged = doc ? dTitle.trim() === doc.title && dContent === doc.content && dCategory === doc.category : true;

  return (
    <ResizableSheet open={docRef !== null} onClose={onClose}>
      <SheetHeader className="space-y-2 border-b bg-muted/30 p-6 pr-12">
        <div className="flex flex-wrap items-center gap-1.5">
          {doc && (
            <>
              <Badge variant="outline">{doc.category}</Badge>
              {archived && <Badge variant="warning">{t('docs.statuses.archived')}</Badge>}
            </>
          )}
        </div>
        <SheetTitle className="text-xl leading-snug">{editing ? t('docs.form.editTitle') : doc?.title || (error ? t('docs.detail.loadError') : '…')}</SheetTitle>
        <SheetDescription className="sr-only">{doc?.title}</SheetDescription>
      </SheetHeader>

      <div className="flex-1 space-y-7 overflow-y-auto p-6">
        {doc?.redirected_from && <p className="rounded-lg border bg-muted/40 p-3 text-xs text-muted-foreground">{t('docs.detail.redirected')}</p>}
        {doc && editing && (
          <div className="space-y-4">
            <Field label={t('docs.form.title')}>
              <input autoFocus className={INPUT} value={dTitle} onChange={(e) => setDTitle(e.target.value)} />
            </Field>
            <Field label={t('docs.form.category')}>
              <select className={INPUT} value={dCategory} onChange={(e) => setDCategory(e.target.value)}>
                {[...new Set([doc.category, ...categories.map((c) => c.slug)])].map((slug) => (
                  <option key={slug} value={slug}>{categories.find((c) => c.slug === slug)?.name ?? slug}</option>
                ))}
              </select>
            </Field>
            <Field label={t('docs.form.content')}>
              <MarkdownEditor value={dContent} onChange={setDContent} />
            </Field>
            <p className="text-xs text-muted-foreground">{t('docs.form.editNote')}</p>
            {actionError && <p role="alert" className="text-sm text-destructive-text">{actionError}</p>}
            <div className="flex justify-end gap-2">
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => setEditing(false)}>{t('docs.actions.cancel')}</Button>
              <Button size="sm" variant="primary" arrow={false} disabled={busy || !dTitle.trim() || !dContent.trim() || unchanged} onClick={() => void save()}>
                {busy ? t('docs.actions.saving') : t('docs.actions.save')}
              </Button>
            </div>
          </div>
        )}

        {doc && !editing && (
          <>
            <section className="space-y-3">
              <div className="flex justify-end">
                {!archived && <Button size="sm" variant="ghost" leadingIcon={Pencil} onClick={startEdit}>{t('docs.actions.edit')}</Button>}
              </div>
              <div className="rounded-xl border bg-card p-5"><Markdown>{doc.content}</Markdown></div>
              {actionError && <p role="alert" className="text-sm text-destructive-text">{actionError}</p>}
            </section>

            <section className="space-y-3">
              <h3 className="text-xs font-bold tracking-wider text-muted-foreground uppercase">{t('docs.detail.details')}</h3>
              <dl className="grid grid-cols-2 gap-3 text-sm">
                {[
                  [t('docs.detail.slug'), <span className="font-mono text-xs">{doc.slug}</span>],
                  [t('docs.createdBy'), doc.created_by ?? '—'],
                  [t('docs.created'), fmt(doc.created_at)],
                  [t('docs.updated'), fmt(doc.updated_at)],
                ].map(([label, value]) => (
                  <div key={String(label)} className="rounded-xl border bg-muted/30 p-3">
                    <dt className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">{label}</dt>
                    <dd className="mt-1 font-semibold">{value}</dd>
                  </div>
                ))}
              </dl>
            </section>

            <section className="space-y-3">
              <h3 className="text-xs font-bold tracking-wider text-muted-foreground uppercase">{t('docs.detail.versions')}</h3>
              {versions.length === 0 ? (
                <p className="text-sm text-muted-foreground">{t('docs.detail.noVersions')}</p>
              ) : (
                <ul className="space-y-2">
                  {versions.map((v) => (
                    <li key={v.version_number} className="rounded-xl border">
                      <div className="flex items-center justify-between gap-3 p-3">
                        <div className="min-w-0">
                          <p className="text-sm font-medium">{t('docs.detail.version', { n: v.version_number })} <span className="font-normal text-muted-foreground">· {fmt(v.created_at)}</span></p>
                          <p className="truncate text-xs text-muted-foreground">{v.title} · {v.category}</p>
                        </div>
                        <Button size="sm" variant="ghost" onClick={() => setShown(shown === v.version_number ? null : v.version_number)}>
                          {shown === v.version_number ? t('docs.detail.hideVersion') : t('docs.detail.showVersion')}
                        </Button>
                      </div>
                      {shown === v.version_number && <div className="border-t bg-muted/20 p-4"><Markdown>{v.content}</Markdown></div>}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </>
        )}
      </div>

      {doc && !editing && (
        <div className="flex items-center justify-between gap-2 border-t p-3">
          <Button size="sm" variant="ghost" className="text-destructive-text" leadingIcon={Trash2} onClick={() => setConfirm('delete')}>{t('docs.actions.delete')}</Button>
          {archived ? (
            <Button size="sm" variant="tertiary" leadingIcon={ArchiveRestore} disabled={busy} onClick={() => void act(() => unarchiveDocument(doc.id), () => load({ category: doc.category, slug: doc.slug }))}>
              {t('docs.actions.unarchive')}
            </Button>
          ) : (
            <Button size="sm" variant="tertiary" leadingIcon={Archive} disabled={busy} onClick={() => setConfirm('archive')}>{t('docs.actions.archive')}</Button>
          )}
        </div>
      )}

      <ConfirmDialog
        open={confirm !== null}
        onOpenChange={(o) => !o && setConfirm(null)}
        title={confirm === 'delete' ? t('docs.deleteTitle') : t('docs.archiveTitle')}
        description={confirm === 'delete' ? t('docs.deleteDesc') : t('docs.archiveDesc')}
        confirmLabel={confirm === 'delete' ? t('docs.actions.delete') : t('docs.actions.archive')}
        cancelLabel={t('docs.actions.cancel')}
        isPending={busy}
        onConfirm={() => {
          if (!doc) return;
          if (confirm === 'delete') void act(() => deleteDocument(doc.id), onClose);
          else void act(() => archiveDocument(doc.id), onClose);
        }}
      />
    </ResizableSheet>
  );
}
