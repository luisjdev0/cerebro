import { Languages } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { LANGS, useLocale } from '../lib/i18n';

/** Segmented EN | ES control; the choice is persisted by i18next's detector. */
export function LanguageSwitcher({ className = '' }: { className?: string }) {
  const { t } = useTranslation();
  const { lang, setLang } = useLocale();
  return (
    <div role="group" aria-label={t('language')} className={`inline-flex items-center gap-1 rounded-lg border bg-background/70 p-0.5 text-xs ${className}`}>
      <Languages className="ml-1.5 size-3.5 text-muted-foreground" aria-hidden />
      {LANGS.map((l) => (
        <button
          key={l.code}
          type="button"
          lang={l.code}
          title={l.label}
          aria-pressed={lang === l.code}
          onClick={() => setLang(l.code)}
          className={`rounded-md px-2 py-1 font-semibold uppercase transition-colors ${
            lang === l.code ? 'bg-foreground text-background' : 'text-muted-foreground hover:text-foreground'
          }`}
        >
          {l.code}
        </button>
      ))}
    </div>
  );
}
