"""The dashboard (NORMAL and FILTER modes), the detail view (DETAIL), and the
confirmation prompt (CONFIRM)."""

from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from typing import ClassVar

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Markdown, Static

from fieldnotes.model import Detail, Item, Section, SourceError, ago, now
from fieldnotes.sources import Source
from fieldnotes.widgets import (
    Body,
    FilterInput,
    HintBar,
    ItemList,
    Panel,
    StatusLine,
    TitleBar,
    state_style,
)

PANELS = Binding.Group("Panel")
SCROLL = Binding.Group("Scroll")
PAGE = Binding.Group("Page")
# Relative times in the panels are redrawn this often, in seconds.
REDRAW_SECONDS = 5


def clock() -> str:
    return f"{now():%a %d %b %H:%M:%S}"


@dataclass
class SourceState:
    data: object = None
    error: str | None = None
    updated: datetime | None = None
    pending: bool = False


class OwnBars:
    """Shows the screen's status line and hints only while it is on top, so a
    prompt over it never leaves two modes on screen."""

    def on_screen_suspend(self) -> None:
        self.show_bars(False)

    def on_screen_resume(self) -> None:
        self.show_bars(True)

    def show_bars(self, shown: bool) -> None:
        for widget in self.query("StatusLine, HintBar"):
            widget.display = shown


class DashboardScreen(OwnBars, Screen):
    HORIZONTAL_BREAKPOINTS: ClassVar[list[tuple[int, str]]] = [
        (0, "-narrow"),
        (110, "-wide"),
        (170, "-very-wide"),
    ]
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("tab,l,right", "next_panel", "Next panel", key_display="tab", group=PANELS),
        Binding(
            "shift+tab,h,left", "previous_panel", "Previous panel", key_display="⇧tab", group=PANELS
        ),
        Binding("1", "jump(1)", "Jump", key_display="1-5", group=PANELS),
        *(Binding(str(number), f"jump({number})", show=False) for number in range(2, 10)),
        Binding("r", "refresh", "Refresh"),
        Binding("R", "refresh_all", "Refresh all"),
        Binding("q", "app.quit", "Quit"),
    ]

    def __init__(self, sources: list[Source]) -> None:
        super().__init__()
        self.sources = {source.name: source for source in sources}
        self.states = {source.name: SourceState() for source in sources}
        self.ticks = 0

    def compose(self) -> ComposeResult:
        yield TitleBar()
        with Body(id="body"):
            for number, source in enumerate(self.sources.values(), 1):
                yield Panel(source, number)
        yield StatusLine()
        yield HintBar()

    @property
    def panels(self) -> list[Panel]:
        return list(self.query(Panel))

    def panel(self, name: str) -> Panel:
        return self.query_one(f"#{name}", Panel)

    @property
    def focused_panel(self) -> Panel | None:
        focused = self.focused
        if focused is None:
            return None
        for panel in self.panels:
            if focused in panel.walk_children():
                return panel
        return None

    @property
    def mode(self) -> str:
        return "FILTER" if isinstance(self.focused, FilterInput) else "NORMAL"

    def on_mount(self) -> None:
        self.query_one(TitleBar).context = socket.gethostname()
        for name, source in self.sources.items():
            self.render_panel(name)
            self.poll(name)
            self.set_interval(source.poll_seconds, partial(self.poll, name))
        self.set_interval(1, self.tick)
        self.tick()
        self.panels[0].list.focus()

    def on_descendant_focus(self) -> None:
        self.update_status()

    def poll(self, name: str) -> None:
        state = self.states[name]
        if state.pending:
            return
        state.pending = True
        self.run_worker(self.fetch(name), group=f"poll-{name}")
        self.update_status()

    async def fetch(self, name: str) -> None:
        state = self.states[name]
        try:
            data = await asyncio.to_thread(self.sources[name].fetch)
        except SourceError as error:
            state.error = str(error)
        except Exception as error:  # noqa: BLE001 - one broken source must not stop the rest
            state.error = f"{type(error).__name__}: {error}"
        else:
            state.data, state.error, state.updated = data, None, now()
        finally:
            state.pending = False
        if self.is_attached:
            self.render_panel(name)
            self.update_status()

    def render_panel(self, name: str) -> None:
        source, state = self.sources[name], self.states[name]
        message = None
        if state.error:
            message = Text(f"Unavailable: {state.error}", "yellow")
            if state.updated:
                message.append(f"\nShowing data from {ago(state.updated)} ago", "dim")
        elif state.updated is None:
            message = Text("Loading…", "dim")
        if state.updated is None:
            self.panel(name).show(source.title, [], message)
            return
        try:
            heading, items = source.heading(state.data), source.items(state.data)
        except Exception as error:  # noqa: BLE001 - show the problem in the panel instead
            heading, items = source.title, []
            message = Text(f"Could not show this data: {error}", "red")
        self.panel(name).show(heading, items, message)

    def tick(self) -> None:
        self.ticks += 1
        self.query_one(TitleBar).clock = clock()
        if self.ticks % REDRAW_SECONDS == 0:
            for name in self.sources:
                self.render_panel(name)
        self.update_status()

    def update_status(self) -> None:
        if not self.is_mounted:
            return
        status = self.query_one(StatusLine)
        status.mode = self.mode
        left = Text()
        panel = self.focused_panel
        if panel is not None:
            left.append(f"{panel.number} {panel.source.title}", "bold")
        for name, source in self.sources.items():
            state = self.states[name]
            if state.updated is None:
                continue
            try:
                summary = source.summary(state.data)
            except Exception:  # noqa: BLE001 - its panel already reports the problem
                summary = None
            if summary:
                text, state_name = summary
                left.append("  ·  " if left else "")
                left.append(text, state_style(state_name))
        status.left = left
        pending = [name for name, state in self.states.items() if state.pending and name != "tasks"]
        failing = [name for name, state in self.states.items() if state.error]
        right = Text()
        if failing:
            right.append(f"{len(failing)} unavailable", "yellow")
            right.append("  ")
        right.append(
            f"refreshing {', '.join(pending)}"
            if pending
            else f"every {self.app.settings.poll_seconds:g}s",
            "dim",
        )
        status.right = right

    def focus_panel(self, index: int) -> None:
        panels = self.panels
        if panels:
            panels[index % len(panels)].list.focus()

    def action_next_panel(self) -> None:
        panel = self.focused_panel
        self.focus_panel(self.panels.index(panel) + 1 if panel else 0)

    def action_previous_panel(self) -> None:
        panel = self.focused_panel
        self.focus_panel(self.panels.index(panel) - 1 if panel else -1)

    def action_jump(self, number: int) -> None:
        if 1 <= number <= len(self.panels):
            self.focus_panel(number - 1)

    def action_refresh(self) -> None:
        panel = self.focused_panel
        if panel is not None:
            self.poll(panel.source.name)

    def action_refresh_all(self) -> None:
        for name in self.sources:
            self.poll(name)

    def on_option_list_option_selected(self, event: ItemList.OptionSelected) -> None:
        item_list = event.option_list
        if isinstance(item_list, ItemList) and item_list.current is not None:
            self.app.push_screen(DetailScreen(item_list.source, item_list.current))


class DetailScreen(OwnBars, Screen):
    """DETAIL mode: everything about one row, loaded in the background."""

    mode = "DETAIL"
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape,enter,q", "app.pop_screen", "Back", key_display="esc"),
        Binding("j,down", "scroll('down')", "Down", key_display="j", group=SCROLL),
        Binding("k,up", "scroll('up')", "Up", key_display="k", group=SCROLL),
        Binding(
            "J,ctrl+d,pagedown,space", "scroll('page_down')", "Down", key_display="J", group=PAGE
        ),
        Binding("K,ctrl+u,pageup", "scroll('page_up')", "Up", key_display="K", group=PAGE),
        Binding("g,home", "scroll('home')", "Top", show=False),
        Binding("G,end", "scroll('end')", "Bottom", show=False),
        Binding("o", "open", "Open"),
        Binding("y", "copy_url", "Copy URL"),
        Binding("Y", "copy_value", "Copy"),
        Binding("x", "rerun", "Rerun failed"),
        Binding("r", "reload", "Reload"),
    ]

    def __init__(self, source: Source, item: Item) -> None:
        super().__init__()
        self.source = source
        self.item = item

    def compose(self) -> ComposeResult:
        yield TitleBar()
        with VerticalScroll(id="detail"):
            yield Static(id="detail-title")
            yield Static(id="detail-fields")
            yield Vertical(id="detail-sections")
        yield StatusLine()
        yield HintBar()

    def on_mount(self) -> None:
        title = self.query_one(TitleBar)
        title.context = f"{self.source.title} › {self.item.title}"
        title.clock = clock()
        self.set_interval(1, self.tick)
        self.query_one(StatusLine).mode = self.mode
        self.show(quick_detail(self.item))
        self.action_reload()

    def tick(self) -> None:
        self.query_one(TitleBar).clock = clock()

    def action_reload(self) -> None:
        status = self.query_one(StatusLine)
        status.left = Text("Loading details…", "dim")
        self.run_worker(self.load(), exclusive=True, group="detail")

    async def load(self) -> None:
        try:
            detail = await asyncio.to_thread(self.source.detail, self.item)
        except Exception as error:  # noqa: BLE001 - keep the row's own facts on screen
            detail = quick_detail(self.item)
            detail.sections.insert(0, Section("Unavailable", Text(str(error), "yellow")))
        if self.is_attached:
            self.show(detail)
            self.query_one(StatusLine).left = Text(
                f"{self.source.title} · loaded {now():%H:%M:%S}", "dim"
            )

    def show(self, detail: Detail) -> None:
        marker_style = state_style(self.item.state)
        self.query_one("#detail-title", Static).update(
            Text.assemble(
                (detail.title, f"bold {marker_style}"), "  ", (self.item.status, marker_style)
            )
        )
        table = Table.grid(padding=(0, 2))
        table.add_column(style="bold", no_wrap=True)
        table.add_column(overflow="fold")
        for name, value in detail.fields:
            table.add_row(name, value)
        self.query_one("#detail-fields", Static).update(table)
        sections = self.query_one("#detail-sections", Vertical)
        sections.remove_children()
        widgets = []
        for section in detail.sections:
            widgets.append(
                Static(Text(section.heading, "bold underline"), classes="detail-heading")
            )
            if section.markdown:
                widgets.append(Markdown(str(section.body), classes="detail-body"))
            else:
                widgets.append(Static(section.body, classes="detail-body", markup=False))
        sections.mount_all(widgets)
        self.refresh_bindings()

    def action_scroll(self, how: str) -> None:
        getattr(self.query_one("#detail", VerticalScroll), f"action_scroll_{how}")()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in ("open", "copy_url"):
            return bool(self.item.url)
        if action == "copy_value":
            return bool(self.item.value)
        if action == "rerun":
            return self.source.can_rerun(self.item)
        return True

    def describe_action(self, action: str) -> str | None:
        if action == "copy_value" and self.item.value_label:
            return f"Copy {self.item.value_label}"
        return None

    def action_open(self) -> None:
        if self.item.url:
            self.app.open_link(self.item.url)

    def action_copy_url(self) -> None:
        if self.item.url:
            self.app.copy(self.item.url, "URL")

    def action_copy_value(self) -> None:
        if self.item.value:
            self.app.copy(self.item.value, self.item.value_label or "value")

    def action_rerun(self) -> None:
        self.app.confirm_rerun(self.source, self.item)


def quick_detail(item: Item) -> Detail:
    """What the row already shows, until the full detail arrives."""
    return Detail(item.title, [("Status", item.status), *(("", line) for line in item.lines)])


class ConfirmScreen(ModalScreen[bool]):
    """CONFIRM mode: a yes-or-no question before an action leaves the machine."""

    mode = "CONFIRM"
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("y", "answer(True)", "Confirm"),
        Binding("n,escape", "answer(False)", "Cancel", key_display="n/esc"),
    ]

    def __init__(self, question: str, detail: str = "") -> None:
        super().__init__()
        self.question = question
        self.detail = detail

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text(self.question, "bold"), id="question")
            if self.detail:
                yield Static(Text(self.detail, "dim"), id="question-detail")
        with Vertical(id="confirm-bars"):
            yield StatusLine()
            yield HintBar()

    def on_mount(self) -> None:
        status = self.query_one(StatusLine)
        status.mode = self.mode
        status.left = Text("Nothing runs until you confirm.", "dim")

    def action_answer(self, answer: bool) -> None:
        self.dismiss(answer)
