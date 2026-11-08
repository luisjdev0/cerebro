import i18n from 'i18next';
import LanguageDetector from 'i18next-browser-languagedetector';
import { initReactI18next, useTranslation } from 'react-i18next';
import { en } from '../locales/en';
import { es } from '../locales/es';

export type Lang = 'en' | 'es';
export const LANGS: { code: Lang; label: string }[] = [
  { code: 'en', label: 'English' },
  { code: 'es', label: 'Español' },
];

// The detector reads the saved choice first (localStorage 'cerebro.lang'), then the
// browser language, and writes every change back, so the language survives navigation
// and reloads.
void i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: { en: { translation: en }, es: { translation: es } },
    supportedLngs: ['en', 'es'],
    nonExplicitSupportedLngs: true,
    load: 'languageOnly',
    fallbackLng: 'en',
    interpolation: { escapeValue: false },
    detection: { order: ['localStorage', 'navigator'], lookupLocalStorage: 'cerebro.lang', caches: ['localStorage'] },
  });

i18n.on('languageChanged', (lng) => {
  document.documentElement.lang = lng;
});
document.documentElement.lang = i18n.resolvedLanguage ?? 'en';

/** Current language (always 'en' | 'es') plus locale-aware date helpers. */
export function useLocale() {
  const { i18n: inst } = useTranslation();
  const lang: Lang = inst.resolvedLanguage === 'es' ? 'es' : 'en';
  return {
    lang,
    setLang: (l: Lang) => void inst.changeLanguage(l),
    formatDate: (iso: string) => new Intl.DateTimeFormat(lang, { dateStyle: 'medium', timeZone: 'UTC' }).format(new Date(iso + 'T00:00:00Z')),
    months: Array.from({ length: 12 }, (_, m) => new Intl.DateTimeFormat(lang, { month: 'short', timeZone: 'UTC' }).format(new Date(Date.UTC(2024, m, 1)))),
  };
}
