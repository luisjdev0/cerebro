import { useLayoutEffect, useRef } from 'react';

/** Nearest ancestor that actually scrolls (the app shell scrolls, not the window). */
function scrollParent(el: HTMLElement): HTMLElement {
  for (let n = el.parentElement; n; n = n.parentElement) {
    const o = getComputedStyle(n).overflowY;
    if ((o === 'auto' || o === 'scroll') && n.scrollHeight > n.clientHeight) return n;
  }
  return (document.scrollingElement as HTMLElement) ?? document.documentElement;
}

/** Keeps the pagination bar under the pointer while paging: the table never gets shorter
 *  (min-height) and the bar is re-anchored after each page change. Same technique as the
 *  Memory and Documents screens. `deps` change when a new page of rows arrives. */
export function useTableAnchor(loading: boolean, rows: unknown) {
  const barRef = useRef<HTMLDivElement>(null);
  const tableRef = useRef<HTMLDivElement>(null);
  const anchorTop = useRef<number | null>(null);

  useLayoutEffect(() => {
    const el = tableRef.current;
    if (!el || loading) return;
    const h = el.offsetHeight;
    if (h > (parseFloat(el.style.minHeight) || 0)) el.style.minHeight = `${h}px`;
  }, [loading, rows]);

  useLayoutEffect(() => {
    const bar = barRef.current;
    if (loading || anchorTop.current === null || !bar) return;
    const delta = bar.getBoundingClientRect().top - anchorTop.current;
    anchorTop.current = null;
    if (delta !== 0) scrollParent(bar).scrollBy({ top: delta, behavior: 'instant' });
  }, [loading, rows]);

  return {
    barRef,
    tableRef,
    resetTableHeight: () => {
      if (tableRef.current) tableRef.current.style.minHeight = '';
    },
    holdBar: () => {
      anchorTop.current = barRef.current?.getBoundingClientRect().top ?? null;
    },
  };
}
