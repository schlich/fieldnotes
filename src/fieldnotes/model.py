"""Shared types and helpers for Fieldnotes' data sources."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from rich.text import Text


class SourceError(Exception):
    """A data source is unavailable; its panel shows the message."""


def capture(command: list[str], *, timeout: float = 12, cwd: str | None = None) -> str:
    """Run a command and return its output, or raise `SourceError`."""
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, cwd=cwd, check=False
        )
    except FileNotFoundError:
        raise SourceError(f"{command[0]} is not installed") from None
    except subprocess.TimeoutExpired:
        raise SourceError(f"{command[0]} timed out after {timeout:g}s") from None
    if result.returncode:
        detail = result.stderr.strip().splitlines()
        raise SourceError(detail[-1] if detail else f"{command[0]} exited with {result.returncode}")
    return result.stdout


def capture_json(command: list[str], *, timeout: float = 12, cwd: str | None = None) -> Any:
    output = capture(command, timeout=timeout, cwd=cwd)
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        raise SourceError(f"{command[0]} returned invalid JSON") from None


def now() -> datetime:
    return datetime.now().astimezone()


def parse_time(value: object) -> datetime | None:
    """An ISO 8601 time in local time; a time without a zone is taken as local."""
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return moment.astimezone()


def span(seconds: float) -> str:
    """A compact duration: `<1m`, `12m`, `3h 05m`, `2d 4h`."""
    minutes = int(seconds // 60)
    if minutes < 1:
        return "<1m"
    if minutes < 60:
        return f"{minutes}m"
    if minutes < 24 * 60:
        return f"{minutes // 60}h {minutes % 60:02d}m"
    return f"{minutes // 1440}d {minutes % 1440 // 60}h"


def ago(moment: datetime | None, at: datetime | None = None) -> str:
    if moment is None:
        return "—"
    return span(max(0.0, ((at or now()) - moment).total_seconds()))


def until(moment: datetime | None, at: datetime | None = None) -> str:
    if moment is None:
        return ""
    seconds = (moment - (at or now())).total_seconds()
    return "now" if seconds <= 0 else span(seconds)


def stamp(moment: datetime | None, at: datetime | None = None) -> str:
    """An absolute local time with its age, for detail views."""
    if moment is None:
        return "—"
    return f"{moment:%a %d %b %H:%M} · {ago(moment, at)} ago"


@dataclass
class Item:
    """One row of a panel."""

    key: str
    """Stable within its panel, so the cursor survives a refresh."""
    title: str
    state: str = "info"
    """Picks the row's marker and color; see `widgets.STATES`."""
    status: str = ""
    """Short text aligned to the right of the title."""
    lines: tuple[str, ...] = ()
    """Dim lines under the title."""
    meter: int | None = None
    """A percentage drawn as a bar under the title."""
    url: str | None = None
    """What `o` opens and `y` copies."""
    value: str | None = None
    """What `Y` copies: a branch, a directory, a note key."""
    value_label: str = ""
    """Names `value` in the key hints, such as `branch`."""
    data: Any = None

    def matches(self, query: str) -> bool:
        """Whether every word of `query` appears somewhere in the row."""
        text = " ".join((self.title, self.status, *self.lines, self.value or ""))
        haystack = text.casefold()
        return all(word in haystack for word in query.casefold().split())


@dataclass
class Section:
    heading: str
    body: str | Text
    markdown: bool = False


@dataclass
class Detail:
    """Everything known about one item, shown in DETAIL mode."""

    title: str
    fields: list[tuple[str, str]] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
