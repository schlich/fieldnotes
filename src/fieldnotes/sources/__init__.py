"""The data behind each panel."""

from __future__ import annotations

from fieldnotes.config import Settings
from fieldnotes.sources.base import Source
from fieldnotes.sources.ci import CISource
from fieldnotes.sources.librarian import LibrarianSource
from fieldnotes.sources.tasks import TasksSource
from fieldnotes.sources.topics import TopicsSource
from fieldnotes.sources.usage import UsageSource

# Panel order; the number keys jump to them in this order.
SOURCES: tuple[type[Source], ...] = (
    UsageSource,
    CISource,
    TopicsSource,
    TasksSource,
    LibrarianSource,
)


def default_sources(settings: Settings) -> list[Source]:
    return [source(settings) for source in SOURCES]


__all__ = ["SOURCES", "Source", "default_sources"]
