import { useEffect, useState, type FormEvent } from 'react';
import { Button, SheetDescription, SheetHeader, SheetTitle } from '@alquimias/ixchel';
import { EyeOff } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '../lib/api';
import { createCategory, createDocument, type Doc } from '../lib/documents';
import { readFiles } from '../lib/importDocs';
import { FileDrop } from './FileDrop';
import { Field, INPUT, useErrorMessage } from './formUi';
import { MarkdownEditor } from './MarkdownEditor';
import { ResizableSheet } from './ResizableSheet';

export type MissingRef = { category: string; slug: string };

const humanize = (slug: string) => slug.replace(/[-_]+/g, ' ').replace(/^\w/, (c) => c.toUpperCase());

/**
 * Creates the document a flow points at (`cerebro-docs://category/slug`). Those categories are
 * hidden from the Documents screen on purpose, so this is the place to upload them: the target is
 * fixed by the reference, and a missing category is created hidden on the way.
 */
export function RefDocPanel({ target, onClose, onCreated }: { target: MissingRef | null; onClose: () => void; onCreated: (d: Doc) => void }) {
  const { t } = useTranslation();
  const errMsg = useErrorMessage();
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    setTitle(target ? humanize(target.slug) : '');
    setContent('');
    setError(undefined);
  }, [target]);

  const take = async (files: File[]) => {
    setError(undefined);
    if (files.length > 1) return setError(t('flows.refDoc.one'));
    const r = await readFiles(files);
    if (r.items.length === 0) return setError(t('docs.import.nothing'));
    setTitle(r.items[0].title);
    setContent(r.items[0].content);
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!target) return;
    setBusy(true);
    setError(undefined);
    const doc = { title: title.trim(), content, category: target.category, slug: target.slug };
    try {
      let d: Doc;
      try {
        d = await createDocument(doc);
      } catch (err) {
        if (!(err instanceof ApiError) || err.status !== 404) throw err;
        await createCategory({ slug: target.category, name: humanize(target.category), hidden: true });
        d = await createDocument(doc);
      }
      onCreated(d);
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <ResizableSheet open={target !== null} onClose={onClose}>
      <SheetHeader className="space-y-1.5 border-b bg-muted/30 p-6 pr-12">
        <SheetTitle className="text-xl leading-snug">{t('flows.refDoc.title')}</SheetTitle>
        <SheetDescription>{t('flows.refDoc.desc')}</SheetDescription>
      </SheetHeader>
      <form id="ref-doc-form" onSubmit={(e) => void submit(e)} className="flex-1 space-y-4 overflow-y-auto p-6">
        <p className="flex items-center gap-2 rounded-lg bg-muted px-3 py-2 font-mono text-xs">
          <EyeOff className="size-4 shrink-0 text-muted-foreground" />
          {target ? `cerebro-docs://${target.category}/${target.slug}` : ''}
        </p>
        <FileDrop multiple={false} disabled={busy} onFiles={(f) => void take(f)} title={t('flows.refDoc.drop')} hint={t('flows.refDoc.dropHint')} />
        <Field label={t('docs.form.title')}>
          <input required className={INPUT} value={title} onChange={(e) => setTitle(e.target.value)} />
        </Field>
        <Field label={t('docs.form.content')}>
          <MarkdownEditor value={content} onChange={setContent} filename={`${target?.slug ?? 'documento'}.md`} />
        </Field>
        {error && <p role="alert" className="text-sm text-destructive-text">{error}</p>}
      </form>
      <div className="flex items-center justify-end gap-2 border-t p-3">
        <Button type="button" variant="ghost" disabled={busy} onClick={onClose}>{t('docs.actions.cancel')}</Button>
        <Button type="submit" form="ref-doc-form" variant="primary" arrow={false} disabled={busy || !title.trim() || !content.trim()}>
          {busy ? t('docs.actions.creating') : t('docs.actions.create')}
        </Button>
      </div>
    </ResizableSheet>
  );
}
