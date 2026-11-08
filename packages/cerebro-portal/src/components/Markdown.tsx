import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

/** Renders a document's markdown with the portal's own typography (see `.md` in index.css).
 *  Raw HTML in the source is not rendered (react-markdown escapes it), links open in a new tab. */
export function Markdown({ children }: { children: string }) {
  return (
    <div className="md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{ a: ({ node: _n, ...props }) => <a {...props} target="_blank" rel="noreferrer noopener" /> }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
