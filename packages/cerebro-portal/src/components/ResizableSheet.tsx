import { useRef, useState, type KeyboardEvent, type PointerEvent as RPointerEvent, type ReactNode } from 'react';
import { Sheet, SheetContent } from '@alquimias/ixchel';
import { useTranslation } from 'react-i18next';

const WIDTH_KEY = 'cerebro.panelWidth';
const MIN_W = 380;
const DEFAULT_W = 560;

const maxWidth = () => Math.min(1100, Math.floor(window.innerWidth * 0.9));
const clamp = (w: number) => Math.max(MIN_W, Math.min(maxWidth(), w));

function loadWidth(): number {
  try {
    const n = Number(localStorage.getItem(WIDTH_KEY));
    return Number.isFinite(n) && n > 0 ? clamp(n) : DEFAULT_W;
  } catch {
    return DEFAULT_W;
  }
}
function saveWidth(w: number) {
  try {
    localStorage.setItem(WIDTH_KEY, String(w));
  } catch {
    /* the width just will not be remembered */
  }
}

/** Right-hand panel whose width the user sets by dragging its left edge. One width is
 *  shared by every panel so the whole screen stays consistent; it is remembered. */
export function ResizableSheet({ open, onClose, children }: { open: boolean; onClose: () => void; children: ReactNode }) {
  const { t } = useTranslation();
  const [width, setWidth] = useState(loadWidth);
  const drag = useRef<{ startX: number; startW: number } | null>(null);

  const onPointerDown = (e: RPointerEvent<HTMLDivElement>) => {
    drag.current = { startX: e.clientX, startW: width };
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: RPointerEvent<HTMLDivElement>) => {
    if (drag.current) setWidth(clamp(drag.current.startW + (drag.current.startX - e.clientX)));
  };
  const onPointerUp = () => {
    if (!drag.current) return;
    drag.current = null;
    saveWidth(width);
  };
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    const w = clamp(width + (e.key === 'ArrowLeft' ? 24 : -24));
    setWidth(w);
    saveWidth(w);
  };

  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" className="w-full gap-0 overflow-hidden p-0 data-[side=right]:sm:max-w-none" style={{ width: `min(${width}px, 100vw)` }}>
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label={t('memory.detail.resize')}
          title={t('memory.detail.resize')}
          tabIndex={0}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onKeyDown={onKeyDown}
          className="group absolute inset-y-0 left-0 z-10 hidden w-2 cursor-col-resize touch-none items-center justify-center sm:flex"
        >
          <span className="h-12 w-1 rounded-full bg-border transition-colors group-hover:bg-brand-soft group-focus-visible:bg-brand-soft" />
        </div>
        {children}
      </SheetContent>
    </Sheet>
  );
}
