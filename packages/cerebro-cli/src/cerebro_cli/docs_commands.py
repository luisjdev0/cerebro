"""`cerebro docs <subcommand>` subcommands (ecosistema-cerebro.md SS11): categories,
full-document CRUD, partial per-section patching and stats -- all via
`DocsClient` (`cerebro_clients`), with no business logic of its own beyond reading
content from a file/stdin and formatting console output.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from cerebro_clients import CerebroAPIError, CerebroConnectionError, DocsClient
from cerebro_memory.markdown_importer import iter_markdown_files

_TITLE_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


def _client() -> DocsClient:
    return DocsClient()


def _read_content(args: argparse.Namespace) -> str:
    """Markdown content from --content-file, or stdin if omitted -- full
    documents aren't practical as a single command-line argument."""
    if args.content_file:
        path = Path(args.content_file)
        if not path.exists():
            print(f"Error: file '{path}' does not exist", file=sys.stderr)
            sys.exit(1)
        return path.read_text(encoding="utf-8")
    if sys.stdin.isatty():
        print("Error: pass --content-file or pipe the content through stdin.", file=sys.stderr)
        sys.exit(1)
    return sys.stdin.read()


def _print_document(doc: dict) -> None:
    print(f"[{doc['category']}/{doc['slug']}] {doc['title']}")
    print(f"  id: {doc['id']}")
    print(f"  created by: {doc.get('created_by') or 'unknown'}  updated: {doc['updated_at']}")


def _print_document_list(docs: list[dict]) -> None:
    if not docs:
        print("(no documents)")
        return
    for d in docs:
        score = f"  score={d['score']:.3f}" if d.get("score") is not None else ""
        print(f"[{d['category']}/{d['slug']}] {d['title']}{score}")


# --------------------------------------------------------------------------- category


def cmd_category_create(args: argparse.Namespace, *, client: DocsClient | None = None) -> None:
    client = client or _client()
    try:
        category = client.create_category(
            args.slug,
            args.name or args.slug,
            description=args.description,
            hidden=args.hidden,
            locked=args.locked,
        )
    except CerebroConnectionError as exc:
        print(f"Could not connect to cerebro-docs: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"The API returned {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)
    flags = " (hidden, locked)" if category["locked"] else " (hidden)" if category["hidden"] else ""
    print(f"Category '{category['slug']}' created{flags}.")


def cmd_category_list(args: argparse.Namespace, *, client: DocsClient | None = None) -> None:
    client = client or _client()
    try:
        categories = client.list_categories()
    except CerebroConnectionError as exc:
        print(f"Could not connect to cerebro-docs: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"The API returned {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)

    if not categories:
        print("(no categories yet)")
        return
    print(f"{'slug':<25} {'name':<30} description")
    print("-" * 90)
    for c in categories:
        print(f"{c['slug']:<25} {c['name']:<30} {c.get('description') or ''}")


def cmd_category_rename(args: argparse.Namespace, *, client: DocsClient | None = None) -> None:
    client = client or _client()
    hidden = True if args.hidden else False if args.visible else None
    try:
        category = client.update_category(
            args.slug, new_slug=args.new_slug, name=args.name, description=args.description, hidden=hidden
        )
    except CerebroConnectionError as exc:
        print(f"Could not connect to cerebro-docs: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"The API returned {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)
    print(f"Category '{args.slug}' -> '{category['slug']}'.")


def cmd_category_delete(args: argparse.Namespace, *, client: DocsClient | None = None) -> None:
    client = client or _client()
    try:
        result = client.delete_category(args.slug, force=args.force)
    except CerebroConnectionError as exc:
        print(f"Could not connect to cerebro-docs: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"The API returned {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)
    print(f"Category '{args.slug}' deleted (documents deleted: {result['documents_deleted']}).")


# --------------------------------------------------------------------------- documents


def cmd_save(args: argparse.Namespace, *, client: DocsClient | None = None) -> None:
    client = client or _client()
    content = _read_content(args)
    try:
        document = client.create_document(args.title, content, args.category, slug=args.slug)
    except CerebroConnectionError as exc:
        print(f"Could not connect to cerebro-docs: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"The API returned {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)
    print(f"Document saved to {document['category']}/{document['slug']} (id={document['id']}).")


def cmd_get(args: argparse.Namespace, *, client: DocsClient | None = None) -> None:
    client = client or _client()
    try:
        document = client.get_document(args.category, args.slug)
    except CerebroConnectionError as exc:
        print(f"Could not connect to cerebro-docs: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"The API returned {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)
    print(document["content"])


def cmd_list(args: argparse.Namespace, *, client: DocsClient | None = None) -> None:
    client = client or _client()
    try:
        if args.archived:
            documents = client.list_archived_documents(category=args.category, limit=args.limit, offset=args.offset)
        else:
            documents = client.list_documents(category=args.category, limit=args.limit, offset=args.offset)
    except CerebroConnectionError as exc:
        print(f"Could not connect to cerebro-docs: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"The API returned {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)
    _print_document_list(documents)


def cmd_search(args: argparse.Namespace, *, client: DocsClient | None = None) -> None:
    client = client or _client()
    try:
        documents = client.list_documents(category=args.category, q=args.query, limit=args.limit, offset=args.offset)
    except CerebroConnectionError as exc:
        print(f"Could not connect to cerebro-docs: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"The API returned {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)
    _print_document_list(documents)


def cmd_update(args: argparse.Namespace, *, client: DocsClient | None = None) -> None:
    client = client or _client()
    content = _read_content(args)
    try:
        document = client.update_document(args.document_id, args.title, content, args.category, slug=args.slug)
    except CerebroConnectionError as exc:
        print(f"Could not connect to cerebro-docs: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"The API returned {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)
    print(f"Document {document['id']} updated ({document['category']}/{document['slug']}).")


def cmd_patch_section(args: argparse.Namespace, *, client: DocsClient | None = None) -> None:
    client = client or _client()
    body = ""
    if args.operation != "delete":
        if args.body_file:
            body = Path(args.body_file).read_text(encoding="utf-8")
        elif args.body is not None:
            body = args.body
        elif not sys.stdin.isatty():
            body = sys.stdin.read()

    try:
        document = client.patch_section(
            args.document_id,
            args.heading,
            args.operation,
            body=body,
            create_if_missing=args.create_if_missing,
            new_heading_level=args.new_heading_level,
        )
    except CerebroConnectionError as exc:
        print(f"Could not connect to cerebro-docs: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"The API returned {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)
    print(f"Section '{args.heading}' ({args.operation}) applied to {document['category']}/{document['slug']}.")


def cmd_archive(args: argparse.Namespace, *, client: DocsClient | None = None) -> None:
    client = client or _client()
    try:
        document = client.archive_document(args.document_id)
    except CerebroConnectionError as exc:
        print(f"Could not connect to cerebro-docs: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"The API returned {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)
    print(f"Document {document['id']} archived ({document['category']}/{document['slug']}).")


def cmd_unarchive(args: argparse.Namespace, *, client: DocsClient | None = None) -> None:
    client = client or _client()
    try:
        document = client.unarchive_document(args.document_id)
    except CerebroConnectionError as exc:
        print(f"Could not connect to cerebro-docs: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"The API returned {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)
    print(f"Document {document['id']} unarchived ({document['category']}/{document['slug']}).")


def cmd_history(args: argparse.Namespace, *, client: DocsClient | None = None) -> None:
    client = client or _client()
    try:
        versions = client.get_document_versions(args.document_id)
    except CerebroConnectionError as exc:
        print(f"Could not connect to cerebro-docs: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"The API returned {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)

    if not versions:
        print("(no previous versions)")
        return
    for v in versions:
        preview = v["content"].splitlines()[0][:80] if v["content"].strip() else ""
        print(f"v{v['version_number']} [{v['category']}] \"{v['title']}\"  {v['created_at']}  {preview}")


def cmd_delete(args: argparse.Namespace, *, client: DocsClient | None = None) -> None:
    client = client or _client()
    if not args.yes:
        answer = input(f"This will delete document {args.document_id} (and its version history). Type 'yes' to continue: ")
        if answer.strip().lower() != "yes":
            print("Cancelled.")
            return

    try:
        client.delete_document(args.document_id)
    except CerebroConnectionError as exc:
        print(f"Could not connect to cerebro-docs: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"The API returned {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)
    print(f"Document {args.document_id} deleted.")


# --------------------------------------------------------------------------- stats


def cmd_stats(args: argparse.Namespace, *, client: DocsClient | None = None) -> None:
    client = client or _client()
    try:
        data = client.get_stats()
    except CerebroConnectionError as exc:
        print(f"Could not connect to cerebro-docs: {exc}", file=sys.stderr)
        sys.exit(1)
    except CerebroAPIError as exc:
        print(f"The API returned {exc.status_code}: {exc.detail}", file=sys.stderr)
        sys.exit(1)

    print("cerebro-docs statistics")
    print("-" * 40)
    print(f"  categories: {data['categories']}")
    print(f"  documents:  {data['documents']}")
    print(f"  versions:   {data['versions']}")


# --------------------------------------------------------------------------- import-markdown


def _derive_title_and_slug(path: Path, text: str) -> tuple[str, str]:
    """Title = the file's first '# heading' if present, otherwise the file
    name. Slug = sanitized file name, passed EXPLICITLY (not derived from the
    title) so it stays stable and traceable to the source file -- see
    luisjdev-pendientes/ecosistema-cerebro, "Bulk markdown importer"."""
    m = _TITLE_RE.search(text)
    title = m.group(1) if m else path.stem
    slug = path.stem.lower().replace("_", "-")
    return title, slug


def cmd_import_markdown(args: argparse.Namespace, *, client: DocsClient | None = None) -> None:
    root = Path(args.path)
    if not root.exists():
        print(f"Error: path '{root}' does not exist", file=sys.stderr)
        sys.exit(1)

    files = iter_markdown_files(root)
    if not files:
        print(f"No .md files found in '{root}'")
        return

    if args.dry_run:
        print(f"[dry-run] {len(files)} file(s) in '{root}' -> category '{args.category}':")
        for f in files:
            title, slug = _derive_title_and_slug(f, f.read_text(encoding="utf-8"))
            print(f"  - [{args.category}/{slug}] \"{title}\" <- {f}")
        return

    client = client or _client()
    imported = skipped = updated = rejected = 0

    for f in files:
        text = f.read_text(encoding="utf-8")
        title, slug = _derive_title_and_slug(f, text)

        existing: dict | None = None
        try:
            existing = client.get_document(args.category, slug)
        except CerebroAPIError as exc:
            if exc.status_code != 404:
                rejected += 1
                print(f"  x error checking for duplicate ({exc.status_code}): \"{title}\" -> {exc.detail}")
                continue
        except CerebroConnectionError as exc:
            print(f"Could not connect to cerebro-docs: {exc}", file=sys.stderr)
            sys.exit(1)

        if existing is not None and not args.update:
            skipped += 1
            print(f"  = ya existe, omitido: \"{title}\" ({args.category}/{slug})")
            continue

        try:
            if existing is not None:
                client.update_document(existing["id"], title, text, args.category, slug=slug)
                updated += 1
                print(f"  ~ actualizado: \"{title}\" ({args.category}/{slug})")
            else:
                client.create_document(title, text, args.category, slug=slug)
                imported += 1
                print(f"  + importado: \"{title}\" ({args.category}/{slug})")
        except CerebroConnectionError as exc:
            rejected += 1
            print(f"  x connection error: \"{title}\" -> {exc}")
        except CerebroAPIError as exc:
            rejected += 1
            print(f"  x rejected ({exc.status_code}): \"{title}\" -> {exc.detail}")

    print()
    print(f"Imported: {imported}  Updated: {updated}  Skipped (already existed): {skipped}  Rejected: {rejected}")
