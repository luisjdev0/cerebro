import { useLayoutEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

/** GitHub-style heading slug, so `[x](#texto-y-énfasis)` matches the heading "Texto y énfasis". */
export function headingSlug(text: string): string {
  return text.trim().toLowerCase().replace(/[^\p{L}\p{N}\p{M}_\- ]/gu, '').replace(/ /g, '-');
}

/** Renders a document's markdown with the portal's own typography (see `.md` in index.css).
 *  Raw HTML in the source is not rendered (react-markdown escapes it). External links open in a
 *  new tab; `#anchor` links scroll to the heading inside this document instead of navigating. */
export function Markdown({ children }: { children: string }) {
  const ref = useRef<HTMLDivElement>(null);

  // Give every heading an id (duplicates get -1, -2…, as GitHub does) so anchors have a target.
  useLayoutEffect(() => {
    const seen = new Map<string, number>();
    ref.current?.querySelectorAll<HTMLElement>('h1,h2,h3,h4,h5,h6').forEach((h) => {
      const base = headingSlug(h.textContent ?? '') || 'section';
      const n = seen.get(base) ?? 0;
      seen.set(base, n + 1);
      h.id = n === 0 ? base : `${base}-${n}`;
    });
  }, [children]);

  const jump = (href: string) => {
    let id = href.slice(1);
    try {
      id = decodeURIComponent(id);
    } catch {
      /* keep the raw fragment */
    }
    const target = Array.from(ref.current?.querySelectorAll<HTMLElement>('[id]') ?? []).find((el) => el.id === id);
    target?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  return (
    <div className="md" ref={ref}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ node: _n, href, ...props }) =>
            href?.startsWith('#') ? (
              <a
                {...props}
                href={href}
                onClick={(e) => {
                  e.preventDefault();
                  jump(href);
                }}
              />
            ) : (
              <a {...props} href={href} target="_blank" rel="noreferrer noopener" />
            ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
