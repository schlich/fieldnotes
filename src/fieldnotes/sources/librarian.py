"""Recent decisions and notes from an IWE knowledge base."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from rich.text import Text

from fieldnotes.model import (
    Detail,
    Item,
    Section,
    SourceError,
    ago,
    capture_json,
    now,
    parse_time,
    stamp,
)
from fieldnotes.sources.base import Source

QUERIES = (
    "{ type: decision }",
    "{ created: { $exists: true } }",
)
# Bare names are frontmatter fields; `$` selects the document's own.
PROJECTION = "key=$key,title=$title,type,created,content=$content"
LIMIT = 6


@dataclass
class Note:
    key: str
    title: str
    type: str
    created: datetime | None
    excerpt: str

    @property
    def summary(self) -> str:
        """The first line of body text, skipping headings."""
        for line in self.excerpt.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            line = line.lstrip("-*• ").strip()
            if line:
                return line
        return "No summary text in this note."


def split_frontmatter(text: str) -> tuple[list[tuple[str, str]], str]:
    """A note's top-level frontmatter fields, and the Markdown after them."""
    if not text.startswith("---\n"):
        return [], text
    end = text.find("\n---", 3)
    if end == -1:
        return [], text
    fields = []
    for line in text[4:end].splitlines():
        name, colon, value = line.partition(":")
        if colon and name.strip() and not line.startswith((" ", "\t", "-")):
            fields.append((name.strip().capitalize(), value.strip().strip("\"'")))
    return fields, text[end + 4 :].lstrip("\n")


def parse(entries: object, seen: set[str]) -> list[Note]:
    if not isinstance(entries, list):
        raise SourceError("IWE returned an unexpected result")
    notes = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("key") or entry["key"] in seen:
            continue
        seen.add(entry["key"])
        notes.append(
            Note(
                key=entry["key"],
                title=entry.get("title") or entry["key"],
                type=entry.get("type") or "note",
                created=parse_time(entry.get("created")),
                excerpt=entry.get("content") or "",
            )
        )
    return notes


class LibrarianSource(Source):
    name = "librarian"
    title = "Librarian / IWE"
    empty = "No decisions or recent notes found."
    # The knowledge base changes slowly.
    interval = 300.0

    def fetch(self) -> list[Note]:
        root = self.settings.iwe_root
        if not root.is_dir():
            raise SourceError(f"Knowledge base not found at {root}")
        notes: list[Note] = []
        seen: set[str] = set()
        for query in QUERIES:
            command = [
                "iwe", "find", "--filter", query, "--sort", "created:-1",
                "--project", PROJECTION, "--max-document-tokens", "60",
                "--limit", str(LIMIT), "--format", "json",
            ]  # fmt: skip
            try:
                entries = capture_json(command, timeout=8, cwd=str(root))
            except SourceError as error:
                raise SourceError(f"IWE unavailable: {error}") from None
            notes += parse(entries, seen)
        return notes

    def items(self, data: list[Note]) -> list[Item]:
        at = now()
        return [
            Item(
                key=note.key,
                title=note.title,
                state="decision" if note.type == "decision" else "note",
                status=f"{note.type} · {ago(note.created, at)}",
                lines=(note.summary,),
                value=note.key,
                value_label="note key",
                data=note,
            )
            for note in data
        ]

    def detail(self, item: Item) -> Detail:
        note: Note = item.data
        fields = [("Key", note.key), ("Created", stamp(note.created))]
        path = self.settings.iwe_root / f"{note.key}.md"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as error:
            return Detail(
                note.title, fields, [Section("Note", Text(f"Unreadable: {error}", "yellow"))]
            )
        frontmatter, body = split_frontmatter(text)
        fields += [field for field in frontmatter if field[0] != "Created"]
        # The detail view already shows the title.
        heading, _, rest = body.partition("\n")
        if heading.strip() == f"# {note.title}":
            body = rest.lstrip("\n")
        return Detail(note.title, fields, [Section("Note", body, markdown=True)])
