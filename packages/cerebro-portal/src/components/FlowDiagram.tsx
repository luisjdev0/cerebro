import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { Check, ChevronDown, Copy, Download, Focus, Maximize, Minimize, ZoomIn, ZoomOut } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { copyText } from '../lib/clipboard';
import { canvasBlob, mermaidBlob, pdfFromCanvas, renderExportSvg, saveBlob, svgSize, svgToCanvas, withPixelSize, type Size } from '../lib/diagramExport';
import { getFlowDiagram } from '../lib/flows';

let counter = 0;
const MIN = 0.05;
const MAX = 4;
const BTN = 'inline-flex h-7 min-w-7 items-center justify-center gap-1 rounded-md border bg-background px-2 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50';

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

type View = { s: number; x: number; y: number };
const clamp = (s: number) => Math.min(MAX, Math.max(MIN, s));

/** Pan / zoom canvas for one SVG: drag to pan, wheel or +/- to zoom around the pointer, fit, 100%, fullscreen. */
function Viewer({ svg, size, fillHeight }: { svg: string; size: Size; fillHeight: boolean }) {
  const { t } = useTranslation();
  const box = useRef<HTMLDivElement>(null);
  const [view, setView] = useState<View>({ s: 1, x: 0, y: 0 });
  const drag = useRef<{ px: number; py: number; x: number; y: number } | null>(null);

  const dims = () => {
    const r = box.current?.getBoundingClientRect();
    return { cw: r?.width ?? 600, ch: r?.height ?? 500 };
  };
  /** Whole diagram visible, or (width) only fitted to the width with the top in view. */
  const fit = useCallback(
    (mode: 'all' | 'width') => {
      const { cw, ch } = dims();
      const pad = 24;
      const sw = (cw - pad) / size.w;
      const s = clamp(mode === 'all' ? Math.min(sw, (ch - pad) / size.h, 1) : Math.min(sw, 1));
      setView({ s, x: (cw - size.w * s) / 2, y: mode === 'all' ? Math.max(pad / 2, (ch - size.h * s) / 2) : pad / 2 });
    },
    [size.w, size.h],
  );
  useLayoutEffect(() => fit('width'), [fit]);

  const zoomAt = (factor: number, cx?: number, cy?: number) =>
    setView((v) => {
      const { cw, ch } = dims();
      const px = cx ?? cw / 2;
      const py = cy ?? ch / 2;
      const s = clamp(v.s * factor);
      const k = s / v.s;
      return { s, x: px - (px - v.x) * k, y: py - (py - v.y) * k };
    });

  // React's onWheel is passive: a native non-passive listener is needed to stop the panel from scrolling.
  useEffect(() => {
    const el = box.current;
    if (!el) return undefined;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      zoomAt(Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0018)), e.clientX - r.left, e.clientY - r.top);
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, []);

  const [full, setFull] = useState(false);
  useEffect(() => {
    const on = () => setFull(document.fullscreenElement === wrap.current);
    document.addEventListener('fullscreenchange', on);
    return () => document.removeEventListener('fullscreenchange', on);
  }, []);
  const wrap = useRef<HTMLDivElement>(null);
  const mounted = useRef(false);
  useEffect(() => {
    if (!mounted.current) {
      mounted.current = true;
      return undefined;
    }
    const id = window.setTimeout(() => fit('all'), 60); // the box changes size entering/leaving fullscreen
    return () => window.clearTimeout(id);
  }, [full]); // eslint-disable-line react-hooks/exhaustive-deps
  const canFullscreen = typeof document !== 'undefined' && !!document.documentElement.requestFullscreen;

  const nudge = (dx: number, dy: number) => setView((v) => ({ ...v, x: v.x + dx, y: v.y + dy }));
  const html = withPixelSize(svg, size);

  return (
    <div ref={wrap} className={`flex flex-col gap-2 bg-background ${full ? 'h-screen p-4' : ''}`}>
      <div className="flex flex-wrap items-center gap-1.5">
        <button type="button" className={BTN} title={t('flows.diagram.zoomOut')} aria-label={t('flows.diagram.zoomOut')} onClick={() => zoomAt(1 / 1.25)}><ZoomOut className="size-4" /></button>
        <span className="min-w-12 text-center text-xs text-muted-foreground tabular-nums">{Math.round(view.s * 100)}%</span>
        <button type="button" className={BTN} title={t('flows.diagram.zoomIn')} aria-label={t('flows.diagram.zoomIn')} onClick={() => zoomAt(1.25)}><ZoomIn className="size-4" /></button>
        <button type="button" className={BTN} title={t('flows.diagram.fit')} aria-label={t('flows.diagram.fit')} onClick={() => fit('all')}><Focus className="size-4" /></button>
        <button type="button" className={BTN} title={t('flows.diagram.actualSize')} onClick={() => setView((v) => { const { cw } = dims(); const k = 1 / v.s; return { s: 1, x: cw / 2 - (cw / 2 - v.x) * k, y: v.y * k }; })}>1:1</button>
        {canFullscreen && (
          <button
            type="button"
            className={BTN}
            title={full ? t('flows.diagram.exitFullscreen') : t('flows.diagram.fullscreen')}
            aria-label={full ? t('flows.diagram.exitFullscreen') : t('flows.diagram.fullscreen')}
            onClick={() => (full ? void document.exitFullscreen() : void wrap.current?.requestFullscreen())}
          >
            {full ? <Minimize className="size-4" /> : <Maximize className="size-4" />}
          </button>
        )}
      </div>
      <div
        ref={box}
        tabIndex={0}
        role="application"
        aria-label={t('flows.detail.diagram')}
        onPointerDown={(e) => {
          drag.current = { px: e.clientX, py: e.clientY, x: view.x, y: view.y };
          e.currentTarget.setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          const d = drag.current;
          if (d) setView((v) => ({ ...v, x: d.x + e.clientX - d.px, y: d.y + e.clientY - d.py }));
        }}
        onPointerUp={() => (drag.current = null)}
        onPointerCancel={() => (drag.current = null)}
        onDoubleClick={() => fit('all')}
        onKeyDown={(e) => {
          if (e.key === '+' || e.key === '=') zoomAt(1.25);
          else if (e.key === '-') zoomAt(1 / 1.25);
          else if (e.key === '0') fit('all');
          else if (e.key === 'ArrowLeft') nudge(60, 0);
          else if (e.key === 'ArrowRight') nudge(-60, 0);
          else if (e.key === 'ArrowUp') nudge(0, 60);
          else if (e.key === 'ArrowDown') nudge(0, -60);
          else return;
          e.preventDefault();
        }}
        className={`relative cursor-grab touch-none overflow-hidden rounded-xl border bg-card outline-none focus-visible:ring-2 focus-visible:ring-ring active:cursor-grabbing ${fillHeight || full ? 'min-h-0 flex-1' : 'h-[32rem]'}`}
      >
        <div
          className="absolute top-0 left-0 origin-top-left select-none"
          style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.s})`, width: size.w, height: size.h }}
          dangerouslySetInnerHTML={{ __html: html }}
        />
      </div>
      <p className="text-[11px] text-muted-foreground">{t('flows.diagram.hint')}</p>
    </div>
  );
}

type Format = 'svg' | 'png' | 'pdf' | 'mmd';

/** Source copy + export menu (SVG / PNG / PDF always light on white, and the Mermaid source). */
function Actions({ source, basename }: { source: string; basename: string }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<Format | null>(null);
  const [failed, setFailed] = useState(false);
  const [copied, setCopied] = useState(false);
  const menu = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return undefined;
    const close = (e: MouseEvent) => !menu.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);

  const run = async (format: Format) => {
    setOpen(false);
    setBusy(format);
    setFailed(false);
    try {
      if (format === 'mmd') {
        saveBlob(mermaidBlob(source), `${basename}.mmd`);
        return;
      }
      const { svg, size } = await renderExportSvg(source);
      if (format === 'svg') saveBlob(new Blob([svg], { type: 'image/svg+xml;charset=utf-8' }), `${basename}.svg`);
      else {
        const canvas = await svgToCanvas(svg, size, 2);
        if (format === 'png') saveBlob(await canvasBlob(canvas, 'image/png'), `${basename}.png`);
        else saveBlob(await pdfFromCanvas(canvas, size.w * 0.75, size.h * 0.75), `${basename}.pdf`);
      }
    } catch (e) {
      console.error('diagram export failed', e);
      setFailed(true);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex items-center gap-1.5">
      {failed && <span role="alert" className="mr-1 text-xs text-destructive-text">{t('flows.diagram.exportError')}</span>}
      <button
        type="button"
        className={BTN}
        title={t('flows.diagram.copySource')}
        aria-label={t('flows.diagram.copySource')}
        onClick={async () => {
          if (await copyText(source)) {
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1800);
          }
        }}
      >
        {copied ? <Check className="size-4 text-brand-soft" /> : <Copy className="size-4" />}
      </button>
      <div ref={menu} className="relative">
        <button type="button" className={BTN} aria-haspopup="menu" aria-expanded={open} disabled={busy !== null} onClick={() => setOpen((o) => !o)}>
          <Download className="size-4" />
          {busy ? t('flows.diagram.exporting') : t('flows.diagram.export')}
          <ChevronDown className="size-3.5" />
        </button>
        {open && (
          <div role="menu" className="absolute right-0 z-20 mt-1 w-52 overflow-hidden rounded-lg border bg-popover p-1 text-sm shadow-lg">
            {(['png', 'svg', 'pdf', 'mmd'] as const).map((f) => (
              <button key={f} type="button" role="menuitem" className="flex w-full items-center rounded-md px-2.5 py-1.5 text-left hover:bg-muted" onClick={() => void run(f)}>
                {t(`flows.diagram.as_${f}`)}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/** The flow drawn as a flowchart. The API generates the Mermaid source from the complete
 *  definition; the Mermaid library (large) is only loaded when someone opens this view. */
export function FlowDiagram({ code, version, leading }: { code: string; version?: number; leading?: ReactNode }) {
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

  const header = (
    <div className="flex items-center justify-between gap-2">
      <div>{leading}</div>
      {source && <Actions source={source} basename={`${code}${version ? `-v${version}` : ''}`} />}
    </div>
  );
  if (error) {
    return (
      <div className="space-y-2">
        {header}
        <p className="rounded-xl border p-4 text-sm text-muted-foreground">{t('flows.diagram.error')}</p>
      </div>
    );
  }
  return (
    <div className="space-y-2">
      {header}
      {svg ? <Viewer key={svg} svg={svg} size={svgSize(svg)} fillHeight={false} /> : <div className="h-40 animate-pulse rounded-xl bg-muted" />}
    </div>
  );
}
