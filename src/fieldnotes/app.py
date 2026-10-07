"""The Fieldnotes application and its command-line entry point."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from collections.abc import Sequence
from typing import ClassVar

from textual.app import App
from textual.binding import Binding, BindingType

from fieldnotes.config import Settings, parse_args
from fieldnotes.model import Item, SourceError
from fieldnotes.screens import ConfirmScreen, DashboardScreen
from fieldnotes.sources import Source, default_sources


class FieldnotesApp(App):
    TITLE = "Fieldnotes"
    CSS_PATH = "fieldnotes.tcss"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+c", "quit", "Quit", show=False, priority=True)
    ]

    def __init__(
        self, settings: Settings | None = None, sources: list[Source] | None = None
    ) -> None:
        super().__init__()
        self.settings = settings or Settings.from_env()
        self.sources = default_sources(self.settings) if sources is None else sources

    def get_default_screen(self) -> DashboardScreen:
        return DashboardScreen(self.sources)

    @property
    def dashboard(self) -> DashboardScreen:
        return next(screen for screen in self.screen_stack if isinstance(screen, DashboardScreen))

    def open_link(self, url: str) -> None:
        """Open `url` in the desktop's browser, or copy it where there is no
        desktop, such as over SSH."""
        opener = shutil.which("xdg-open")
        if opener and (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
            subprocess.Popen(
                [opener, url],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self.notify(url, title="Opened")
        else:
            self.copy_to_clipboard(url)
            self.notify(url, title="No desktop browser here, so the URL was copied")

    def copy(self, text: str, label: str) -> None:
        """Copy through the terminal (OSC 52), which also works over SSH."""
        self.copy_to_clipboard(text)
        self.notify(text if len(text) <= 120 else f"{text[:119]}…", title=f"Copied {label}")

    def confirm_rerun(self, source: Source, item: Item) -> None:
        def answered(confirmed: bool | None) -> None:
            if confirmed:
                self.run_worker(self.rerun(source, item), group="rerun")

        self.push_screen(
            ConfirmScreen(
                f"Rerun the failed jobs of {item.title}?",
                "GitHub Actions runs them again on the same commit.",
            ),
            answered,
        )

    async def rerun(self, source: Source, item: Item) -> None:
        try:
            message = await asyncio.to_thread(source.rerun, item)
        except SourceError as error:
            self.notify(str(error), title="Rerun failed", severity="error")
            return
        self.notify(message, title="Rerun requested")
        self.dashboard.poll(source.name)


def main(argv: Sequence[str] | None = None) -> None:
    FieldnotesApp(parse_args(argv)).run()
