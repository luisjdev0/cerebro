import { useState } from 'react';
import { Check, Copy } from 'lucide-react';
import { useTranslation } from 'react-i18next';

/** A read-only command line with a copy button. */
export function CodeBlock({ code }: { code: string }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable (insecure context): the text stays selectable */
    }
  };
  return (
    <div className="flex items-center gap-2 rounded-lg border bg-background/70 py-1.5 pr-1.5 pl-3 dark:bg-black/20">
      <code className="min-w-0 flex-1 overflow-x-auto font-mono text-xs whitespace-nowrap select-all">{code}</code>
      <button
        type="button"
        onClick={() => void copy()}
        aria-label={t('login.copy')}
        title={copied ? t('login.copied') : t('login.copy')}
        className="shrink-0 rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
      >
        {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
      </button>
    </div>
  );
}
