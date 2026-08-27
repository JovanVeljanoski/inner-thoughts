#!/usr/bin/env python3
"""Validate the internal structure of the static site using only Python's stdlib."""

from __future__ import annotations

import sys
from collections import Counter, deque
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "index.html"
IGNORED_PARTS = {".git", "_site"}


class Document(HTMLParser):
    def __init__(self, path: Path) -> None:
        super().__init__(convert_charrefs=True)
        self.path = path
        self.has_html_doctype = False
        self.ids: list[str] = []
        self.references: list[tuple[str, str, int]] = []
        self._in_title = False
        self._title_parts: list[str] = []

    @property
    def title(self) -> str:
        return "".join(self._title_parts).strip()

    def handle_decl(self, decl: str) -> None:
        if decl.strip().lower() == "doctype html":
            self.has_html_doctype = True

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "title":
            self._in_title = True

        for name, value in attrs:
            if value is None:
                continue
            name = name.lower()
            if name == "id":
                self.ids.append(value)
            if name == "href" and tag.lower() in {"a", "link", "area"}:
                self.references.append((name, value, self.getpos()[0]))
            elif name in {"src", "poster"}:
                self.references.append((name, value, self.getpos()[0]))

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def local_target(source: Path, reference: str) -> tuple[Path, str] | None:
    """Resolve a local URL to a filesystem path and fragment."""
    parts = urlsplit(reference.strip())
    if parts.scheme or parts.netloc or reference.startswith("//"):
        return None

    raw_path = unquote(parts.path)
    target = source if not raw_path else source.parent / raw_path
    target = target.resolve()

    try:
        target.relative_to(ROOT)
    except ValueError:
        return target, unquote(parts.fragment)

    if target.is_dir():
        target /= "index.html"
    return target, unquote(parts.fragment)


def main() -> int:
    html_files = sorted(
        path.resolve()
        for path in ROOT.rglob("*.html")
        if not any(part in IGNORED_PARTS for part in path.relative_to(ROOT).parts)
    )

    errors: list[str] = []
    documents: dict[Path, Document] = {}

    if not INDEX.exists():
        errors.append("index.html: root gateway is missing")

    for path in html_files:
        document = Document(path)
        try:
            document.feed(path.read_text(encoding="utf-8"))
            document.close()
        except (OSError, UnicodeError) as exc:
            errors.append(f"{relative(path)}: could not be read as UTF-8: {exc}")
            continue

        documents[path] = document
        if not document.has_html_doctype:
            errors.append(f"{relative(path)}: missing <!doctype html>")
        if not document.title:
            errors.append(f"{relative(path)}: missing a non-empty <title>")

        duplicates = sorted(item for item, count in Counter(document.ids).items() if count > 1)
        for duplicate in duplicates:
            errors.append(f'{relative(path)}: duplicate id "{duplicate}"')

    graph: dict[Path, set[Path]] = {path: set() for path in documents}

    for source, document in documents.items():
        for attribute, reference, line in document.references:
            resolved = local_target(source, reference)
            if resolved is None:
                continue

            target, fragment = resolved
            location = f"{relative(source)}:{line}"
            if not target.exists():
                try:
                    display_target = relative(target)
                except ValueError:
                    display_target = str(target)
                errors.append(
                    f'{location}: broken {attribute}="{reference}" '
                    f"(target does not exist: {display_target})"
                )
                continue

            if target.suffix.lower() == ".html" and target in documents:
                graph[source].add(target)
                if fragment and fragment not in documents[target].ids:
                    errors.append(
                        f'{location}: fragment "#{fragment}" does not exist in '
                        f"{relative(target)}"
                    )

    # Every HTML experience should be discoverable by following links from index.html.
    index_path = INDEX.resolve()
    if index_path in documents:
        visited: set[Path] = set()
        queue: deque[Path] = deque([index_path])
        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            queue.extend(graph.get(current, set()) - visited)

        for orphan in sorted(set(documents) - visited):
            errors.append(
                f"{relative(orphan)}: page is not reachable from the root index.html"
            )

    if errors:
        print(f"Site validation failed with {len(errors)} error(s):", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print(
        f"Site validation passed: {len(documents)} HTML page(s), "
        "all internal links and entry points are valid."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
