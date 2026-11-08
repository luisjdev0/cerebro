import { useTranslation } from 'react-i18next';

const LINKS = {
  author: 'https://github.com/luisjdev0',
  license: 'https://github.com/luisjdev0/cerebro/blob/main/LICENSE',
  repo: 'https://github.com/luisjdev0/cerebro',
};

/** Creator + license line. `login` is the prominent version (side panel of the login);
 *  `compact` is the discreet one shown at the bottom of every screen inside the portal. */
export function Credits({ variant = 'compact', className = '' }: { variant?: 'login' | 'compact'; className?: string }) {
  const { t } = useTranslation();
  const compact = variant === 'compact';
  const link = 'hover:underline focus-visible:underline';
  return (
    <footer className={`flex flex-wrap items-center gap-x-2 gap-y-1 text-muted-foreground ${compact ? 'justify-center text-[11px] opacity-70 hover:opacity-100' : 'border-t pt-4 text-xs dark:border-white/10'} ${className}`}>
      <span>
        {t('login.createdBy')}{' '}
        <a href={LINKS.author} target="_blank" rel="noreferrer" className={`${compact ? '' : 'font-semibold text-foreground dark:text-white'} ${link}`}>
          luisjdev
        </a>
      </span>
      <span aria-hidden>·</span>
      <a href={LINKS.license} target="_blank" rel="noreferrer" className={link}>
        {t('login.license')}
      </a>
      <span aria-hidden>·</span>
      <a href={LINKS.repo} target="_blank" rel="noreferrer" className={link}>
        GitHub
      </a>
    </footer>
  );
}
