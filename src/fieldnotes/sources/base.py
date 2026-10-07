from __future__ import annotations

from typing import Any, ClassVar

from fieldnotes.config import Settings
from fieldnotes.model import Detail, Item


class Source:
    """One panel's data: how to fetch it, list it, and describe one item.

    `fetch`, `detail`, and `rerun` block, so the app runs them in worker
    threads; `items`, `heading`, and `summary` are cheap and run on every
    redraw so that relative times stay current.
    """

    name: ClassVar[str]
    title: ClassVar[str]
    empty: ClassVar[str] = "Nothing to show."
    interval: ClassVar[float | None] = None
    """Seconds between refreshes; `None` uses `Settings.poll_seconds`."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def poll_seconds(self) -> float:
        return self.interval or self.settings.poll_seconds

    def fetch(self) -> Any:
        raise NotImplementedError

    def items(self, data: Any) -> list[Item]:
        raise NotImplementedError

    def heading(self, data: Any) -> str:
        return self.title

    def summary(self, data: Any) -> tuple[str, str] | None:
        """A `(text, state)` fragment for the status line, if worth showing."""
        return None

    def detail(self, item: Item) -> Detail:
        return Detail(item.title, [("Status", item.status), *[("", line) for line in item.lines]])

    def can_rerun(self, item: Item) -> bool:
        return False

    def rerun(self, item: Item) -> str:
        raise NotImplementedError
