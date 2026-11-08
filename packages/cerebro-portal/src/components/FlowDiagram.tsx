import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { getFlowDiagram } from '../lib/flows';
import { CopyButton, DownloadButton } from './MarkdownView';

let counter = 0;

/** Whether the app is in dark mode (the shell toggles a `dark` class on <html>). */
function useIsDark(): boolean {
  const read = () => document.documentElement.classList.contains('dark');
  const [dark, setDark] = useState(read);
  useEffect(() => {
    const obs = new MutationObserver(() => setDark(read()));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
    return () => obs.disconnect();
  }, []);
  return dark;
}

/** The flow drawn as a flowchart. The API generates the Mermaid source from the complete
 *  definition; the Mermaid library (large) is only loaded when someone opens this view. */
export function FlowDiagram({ code, version }: { code: string; version?: number }) {
  const { t } = useTranslation();
  const dark = useIsDark();
  const [source, setSource] = useState<string | null>(null);
  const [svg, setSvg] = useState<string | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setSource(null);
    setError(false);
    getFlowDiagram(code, version).then(
      (r) => !cancelled && setSource(r.diagram),
      () => !cancelled && setError(true),
    );
    return () => {
      cancelled = true;
    };
  }, [code, version]);

  useEffect(() => {
    if (source === null) return undefined;
    let cancelled = false;
    setSvg(null);
    (async () => {
      try {
        const { default: mermaid } = await import('mermaid');
        mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: dark ? 'dark' : 'default', flowchart: { htmlLabels: true, curve: 'basis' } });
        const out = await mermaid.render(`flow-diagram-${++counter}`, source);
        if (!cancelled) setSvg(out.svg);
      } catch {
        if (!cancelled) setError(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [source, dark]);

  if (error) return <p className="rounded-xl border p-4 text-sm text-muted-foreground">{t('flows.diagram.error')}</p>;
  return (
    <div className="space-y-2">
      <div className="flex justify-end gap-1.5">
        {source && <CopyButton text={source} label={t('flows.diagram.copySource')} />}
        {svg && <DownloadButton text={svg} filename={`${code}${version ? `-v${version}` : ''}.svg`} label={t('flows.diagram.downloadSvg')} mime="image/svg+xml" />}
      </div>
      <div className="max-h-[32rem] overflow-auto rounded-xl border bg-card p-4">
        {svg ? <div className="mx-auto w-fit [&_svg]:h-auto [&_svg]:max-w-none" dangerouslySetInnerHTML={{ __html: svg }} /> : <div className="h-40 animate-pulse rounded bg-muted" />}
      </div>
    </div>
  );
}
