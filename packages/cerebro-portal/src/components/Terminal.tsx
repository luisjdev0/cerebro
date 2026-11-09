import { useState } from 'react';
import { Check, Copy } from 'lucide-react';
import { useTranslation } from 'react-i18next';

type OS = 'linux' | 'windows';
const detectOS = (): OS => (/windows/i.test(navigator.userAgent) ? 'windows' : 'linux');

function Line({ prompt, code }: { prompt: string; code: string }) {
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
    <div className="group flex items-center gap-2">
      <span className="select-none text-emerald-400">{prompt}</span>
      <code className="min-w-0 flex-1 overflow-x-auto whitespace-nowrap select-all">{code}</code>
      <button
        type="button"
        onClick={() => void copy()}
        aria-label={t('login.copy')}
        title={copied ? t('login.copied') : t('login.copy')}
        className="shrink-0 rounded p-1 text-slate-400 transition-colors hover:bg-white/10 hover:text-white"
      >
        {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
      </button>
    </div>
  );
}

/** Terminal-styled card with the install command (per OS) and the login command. */
export function Terminal({ origin }: { origin: string }) {
  const { t } = useTranslation();
  const [os, setOs] = useState<OS>(detectOS);
  const install =
    os === 'windows'
      ? { prompt: '>', code: `irm ${origin}/install/install.ps1 | iex` }
      : { prompt: '$', code: `curl -fsSL ${origin}/install/install.sh | sh` };
  return (
    <div className="overflow-hidden rounded-xl border border-slate-700/60 bg-slate-950 text-[13px] text-slate-100 shadow-lg">
      <div className="flex items-center gap-3 border-b border-white/10 bg-white/[0.04] px-3 py-2">
        <div className="flex gap-1.5" aria-hidden>
          <span className="size-2.5 rounded-full bg-red-400/80" />
          <span className="size-2.5 rounded-full bg-amber-400/80" />
          <span className="size-2.5 rounded-full bg-emerald-400/80" />
        </div>
        <div role="tablist" className="ml-auto flex gap-1 text-xs">
          {(['linux', 'windows'] as const).map((k) => (
            <button
              key={k}
              type="button"
              role="tab"
              aria-selected={os === k}
              onClick={() => setOs(k)}
              className={`rounded-md px-2.5 py-1 font-semibold transition-colors ${os === k ? 'bg-white/15 text-white' : 'text-slate-400 hover:text-white'}`}
            >
              {k === 'linux' ? 'Linux' : 'Windows'}
            </button>
          ))}
        </div>
      </div>
      <div className="space-y-3 p-4 font-mono">
        <div className="space-y-1">
          <p className="font-sans text-[11px] text-slate-400"># {t('login.cliInstall')}</p>
          <Line {...install} />
        </div>
        <div className="space-y-1">
          <p className="font-sans text-[11px] text-slate-400"># {t('login.cliLogin')}</p>
          <Line prompt={os === 'windows' ? '>' : '$'} code={`cerebro login --url ${origin}`} />
        </div>
      </div>
    </div>
  );
}
