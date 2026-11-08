import { useEffect, useRef, useState } from 'react';
import { Check, Copy, Download } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { copyText } from '../lib/clipboard';
import { Markdown } from './Markdown';

/** Small "copy markdown" icon button that confirms with a check for a moment. */
export function CopyButton({ text }: { text: string }) {
  const { t } = useTranslation();
  const [done, setDone] = useState(false);
  const timer = useRef<number>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const label = done ? t('docs.form.copied') : t('docs.form.copy');
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      onClick={async () => {
        if (await copyText(text)) {
          setDone(true);
          window.clearTimeout(timer.current);
          timer.current = window.setTimeout(() => setDone(false), 1800);
        }
      }}
      className={ICON_BTN}
    >
      {done ? <Check className="size-3.5 text-brand-soft" /> : <Copy className="size-3.5" />}
    </button>
  );
}

const ICON_BTN = 'inline-flex size-7 items-center justify-center rounded-md border text-muted-foreground transition-colors hover:bg-muted hover:text-foreground';

/** Icon button that saves the markdown as a `.md` file. */
export function DownloadButton({ text, filename }: { text: string; filename: string }) {
  const { t } = useTranslation();
  const label = t('docs.form.download');
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      className={ICON_BTN}
      onClick={() => {
        const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown;charset=utf-8' }));
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      }}
    >
      <Download className="size-3.5" />
    </button>
  );
}

/** Copy + download, side by side. */
export function CopyDownload({ text, filename }: { text: string; filename: string }) {
  return (
    <div className="flex items-center gap-1.5">
      <CopyButton text={text} />
      <DownloadButton text={text} filename={filename} />
    </div>
  );
}

/** Segmented switch used by the viewer and the editor. */
export function ViewTabs({ items, value, onChange }: { items: { id: string; label: string }[]; value: string; onChange: (id: string) => void }) {
  return (
    <div className="inline-flex gap-1 rounded-lg border p-0.5">
      {items.map((i) => (
        <button
          key={i.id}
          type="button"
          onClick={() => onChange(i.id)}
          className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${value === i.id ? 'bg-foreground text-background' : 'text-muted-foreground hover:text-foreground'}`}
        >
          {i.label}
        </button>
      ))}
    </div>
  );
}

/** Read-only document body: rendered preview or the raw markdown source, with a copy button. */
export function MarkdownView({ value, filename = 'documento.md' }: { value: string; filename?: string }) {
  const { t } = useTranslation();
  const [mode, setMode] = useState<'preview' | 'raw'>('preview');
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <ViewTabs
          items={[{ id: 'preview', label: t('docs.form.preview') }, { id: 'raw', label: t('docs.form.raw') }]}
          value={mode}
          onChange={(id) => setMode(id as 'preview' | 'raw')}
        />
        <CopyDownload text={value} filename={filename} />
      </div>
      <div className="rounded-xl border bg-card p-5">
        {mode === 'preview' ? (
          <Markdown>{value}</Markdown>
        ) : (
          <pre className="font-mono text-[13px] leading-relaxed break-words whitespace-pre-wrap">{value}</pre>
        )}
      </div>
    </div>
  );
}
