"""Panels, the status line, and the key-hint bar."""

from __future__ import annotations

from itertools import groupby
from typing import TYPE_CHECKING, ClassVar

from rich.console import RenderableType
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text
from textual.binding import Binding, BindingType
from textual.containers import ScrollableContainer, Vertical
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from fieldnotes.model import Item

if TYPE_CHECKING:
    from textual.screen import Screen

    from fieldnotes.sources import Source

# Marker and Rich style for each `Item.state`.
STATES: dict[str, tuple[str, str]] = {
    "failed": ("✗", "bold red"),
    "running": ("●", "yellow"),
    "passed": ("✓", "green"),
    "retired": ("○", "dim"),
    "waiting": ("●", "green"),
    "stale": ("●", "yellow"),
    "done": ("✓", "green"),
    "silent": ("?", "yellow"),
    "warn": ("○", "yellow"),
    "over": ("▲", "red"),
    "on-track": ("●", "yellow"),
    "under": ("▼", "blue"),
    "decision": ("◆", "magenta"),
    "note": ("•", "cyan"),
    "info": ("•", "cyan"),
}


def state_style(state: str) -> str:
    return STATES.get(state, STATES["info"])[1]


def render_item(item: Item) -> RenderableType:
    marker, style = STATES.get(item.state, STATES["info"])
    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
    grid.add_column(no_wrap=True, justify="right")
    grid.add_row(
        Text.assemble((marker, style), " ", (item.title, "bold")),
        Text(item.status, style),
    )
    if item.meter is not None:
        bar = ProgressBar(
            total=100,
            completed=item.meter,
            style="grey30",
            complete_style=style,
            finished_style=style,
        )
        grid.add_row(bar, "")
    for line in item.lines:
        grid.add_row(Text(f"  {line}", "dim"), "")
    return grid


MOVE = Binding.Group("Move")


class ItemList(OptionList, inherit_bindings=False):
    """A panel's rows. Every key that acts on the highlighted row lives here,
    so the hint bar shows exactly what works on it."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("j,down", "cursor_down", "Down", key_display="j", group=MOVE),
        Binding("k,up", "cursor_up", "Up", key_display="k", group=MOVE),
        Binding("g,home", "first", "Top", show=False),
        Binding("G,end", "last", "Bottom", show=False),
        Binding("ctrl+d,pagedown", "page_down", "Page down", show=False),
        Binding("ctrl+u,pageup", "page_up", "Page up", show=False),
        Binding("enter", "select", "Details"),
        Binding("o", "open", "Open"),
        Binding("y", "copy_url", "Copy URL"),
        Binding("Y", "copy_value", "Copy"),
        Binding("x", "rerun", "Rerun failed"),
        Binding("slash", "filter", "Filter"),
        Binding("escape", "clear_filter", "Clear filter"),
    ]

    def __init__(self, source: Source, **kwargs) -> None:
        super().__init__(compact=True, **kwargs)
        self.source = source
        self.items: list[Item] = []

    @property
    def panel(self) -> Panel:
        return self.query_ancestor(Panel)

    @property
    def current(self) -> Item | None:
        index = self.highlighted
        return self.items[index] if index is not None and index < len(self.items) else None

    def show(self, items: list[Item]) -> None:
        """Show `items`, keeping the cursor on the same row when it survives."""
        previous = self.current.key if self.current else None
        if [item.key for item in items] == [item.key for item in self.items]:
            self.items = items
            for index, item in enumerate(items):
                self.replace_option_prompt_at_index(index, render_item(item))
        else:
            self.items = items
            self.set_options(Option(render_item(item)) for item in items)
            keys = [item.key for item in items]
            if previous in keys:
                self.highlighted = keys.index(previous)
            else:
                self.highlighted = 0 if items else None
        self.refresh_bindings()

    def watch_highlighted(self, highlighted: int | None) -> None:
        super().watch_highlighted(highlighted)
        self.refresh_bindings()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        item = self.current
        if action in ("open", "copy_url"):
            return bool(item and item.url)
        if action == "copy_value":
            return bool(item and item.value)
        if action == "rerun":
            return bool(item and self.source.can_rerun(item))
        if action == "clear_filter":
            return bool(self.panel.filter)
        if action == "select":
            return True if item else None
        return True

    def describe_action(self, action: str) -> str | None:
        """A hint that names what the key acts on for this row."""
        item = self.current
        if action == "copy_value" and item and item.value_label:
            return f"Copy {item.value_label}"
        return None

    def action_open(self) -> None:
        if self.current and self.current.url:
            self.app.open_link(self.current.url)

    def action_copy_url(self) -> None:
        if self.current and self.current.url:
            self.app.copy(self.current.url, "URL")

    def action_copy_value(self) -> None:
        if self.current and self.current.value:
            self.app.copy(self.current.value, self.current.value_label or "value")

    def action_rerun(self) -> None:
        if self.current and self.source.can_rerun(self.current):
            self.app.confirm_rerun(self.source, self.current)

    def action_filter(self) -> None:
        self.panel.start_filter()

    def action_clear_filter(self) -> None:
        self.panel.set_filter("")


class FilterInput(Input):
    """FILTER mode: typing narrows the panel's rows as you go."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter", "apply", "Keep filter"),
        Binding("escape", "cancel", "Clear filter"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(placeholder="filter rows", compact=True, **kwargs)

    @property
    def panel(self) -> Panel:
        return self.query_ancestor(Panel)

    def on_input_changed(self, event: Input.Changed) -> None:
        event.stop()
        self.panel.set_filter(event.value)

    def action_apply(self) -> None:
        self.panel.finish_filter()

    def action_cancel(self) -> None:
        self.panel.set_filter("")
        self.panel.finish_filter()

    def on_blur(self) -> None:
        # Leaving the field any other way, such as Tab, keeps the filter.
        self.panel.finish_filter(refocus=False)


class Panel(Vertical):
    def __init__(self, source: Source, number: int) -> None:
        super().__init__(id=source.name, classes="panel")
        self.source = source
        self.number = number
        self.filter = ""
        self.heading = source.title
        self.items: list[Item] = []
        self.message: Text | None = None

    def compose(self):
        yield Static("", classes="message", markup=False)
        yield ItemList(self.source, id=f"{self.source.name}-list")
        yield FilterInput(id=f"{self.source.name}-filter")

    @property
    def list(self) -> ItemList:
        return self.query_one(ItemList)

    @property
    def filtering(self) -> bool:
        return self.has_class("-filtering")

    def show(self, heading: str, items: list[Item], message: Text | None = None) -> None:
        self.heading, self.items, self.message = heading, items, message
        self.redraw()

    def redraw(self) -> None:
        shown = [item for item in self.items if item.matches(self.filter)]
        self.border_title = f"{self.number} {self.heading}"
        self.border_subtitle = (
            f"/{self.filter}  {len(shown)} of {len(self.items)}" if self.filter else ""
        )
        message = self.message
        if message is None and not self.items:
            message = Text(self.source.empty, "dim")
        elif message is None and not shown:
            message = Text("No rows match the filter.", "dim")
        self.query_one(".message", Static).update(message or "")
        self.set_class(message is not None, "-has-message")
        self.list.show(shown)

    def start_filter(self) -> None:
        field = self.query_one(FilterInput)
        self.add_class("-filtering")
        with field.prevent(Input.Changed):
            field.value = self.filter
        field.focus()
        self.screen.refresh_bindings()

    def set_filter(self, text: str) -> None:
        self.filter = text.strip()
        self.redraw()

    def finish_filter(self, refocus: bool = True) -> None:
        if not self.filtering:
            return
        self.remove_class("-filtering")
        if refocus:
            self.list.focus()
        self.screen.refresh_bindings()


class Body(ScrollableContainer, can_focus=False, inherit_bindings=False):
    """Holds the panels; the screen's CSS lays them out by width."""


class TitleBar(Widget):
    """The app name and what the screen shows on the left, a clock on the right."""

    context: reactive[str] = reactive("")
    clock: reactive[str] = reactive("")

    def render(self) -> RenderableType:
        grid = Table.grid(expand=True)
        grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        grid.add_column(no_wrap=True, justify="right")
        title = Text.assemble(("Fieldnotes", "bold"))
        if self.context:
            title.append(f"  {self.context}", "dim")
        grid.add_row(title, Text(self.clock, "dim"))
        return grid


MODES = ("normal", "filter", "detail", "confirm")


class StatusLine(Widget):
    """The current mode as a labeled badge, then what the screen reports."""

    COMPONENT_CLASSES: ClassVar[set[str]] = {f"status-line--{mode}" for mode in MODES}

    mode: reactive[str] = reactive("NORMAL")
    left: reactive[Text] = reactive(Text, always_update=True)
    right: reactive[Text] = reactive(Text, always_update=True)

    def render(self) -> RenderableType:
        badge = Text(
            f" {self.mode} ",
            self.get_component_rich_style(f"status-line--{self.mode.lower()}"),
        )
        grid = Table.grid(expand=True)
        grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        grid.add_column(no_wrap=True, justify="right")
        grid.add_row(Text.assemble(badge, " ", self.left), self.right)
        return grid


def binding_chips(screen: Screen) -> list[tuple[str, str, bool]]:
    """`(keys, description, enabled)` for every key that works right now.

    Keys for the same action show once; consecutive actions in one
    `Binding.Group` share a chip, such as `j/k Move`.
    """
    app = screen.app
    actions: dict[str, tuple[list[Binding], object, bool]] = {}
    for active in screen.active_bindings.values():
        if not active.binding.show:
            continue
        bindings, _, _ = actions.setdefault(
            active.binding.action, ([], active.node, active.enabled)
        )
        bindings.append(active.binding)

    def keys(binding: Binding) -> str:
        return binding.key_display or app.get_key_display(binding)

    chips = []
    for group, entries in groupby(actions.items(), key=lambda entry: entry[1][0][0].group):
        entries = list(entries)
        if group is not None and len(entries) > 1:
            chips.append(
                (
                    "/".join(keys(bindings[0]) for _, (bindings, _, _) in entries),
                    group.description,
                    all(enabled for _, (_, _, enabled) in entries),
                )
            )
            continue
        for action, (bindings, node, enabled) in entries:
            describe = getattr(node, "describe_action", None)
            description = (describe(action) if describe else None) or bindings[0].description
            chips.append((keys(bindings[0]), description, enabled))
    return chips


class HintBar(Widget):
    """Every key that works in the current mode and focus, wrapped onto as
    many lines as the width needs; a chip never splits across lines."""

    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "hint-bar--key",
        "hint-bar--description",
        "hint-bar--disabled",
    }
    SEPARATOR = "   "

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.chips: list[tuple[str, str, bool]] = []

    def on_mount(self) -> None:
        self.screen.bindings_updated_signal.subscribe(self, self.bindings_changed)
        self.collect()

    def on_unmount(self) -> None:
        self.screen.bindings_updated_signal.unsubscribe(self)

    def bindings_changed(self, screen: Screen) -> None:
        if self.is_attached and screen is self.screen:
            self.call_after_refresh(self.collect)

    def collect(self) -> None:
        # A deferred call can arrive after the screen has been dismissed.
        if not self.is_attached:
            return
        chips = binding_chips(self.screen)
        if chips != self.chips:
            self.chips = chips
            self.refresh(layout=True)

    def lines(self, width: int) -> list[list[tuple[str, str, bool]]]:
        lines: list[list[tuple[str, str, bool]]] = [[]]
        used = 0
        for chip in self.chips:
            size = len(chip[0]) + 1 + len(chip[1])
            gap = len(self.SEPARATOR) if lines[-1] else 0
            if lines[-1] and used + gap + size > width:
                lines.append([])
                used, gap = 0, 0
            lines[-1].append(chip)
            used += gap + size
        return lines

    def get_content_height(self, container, viewport, width: int) -> int:
        return max(1, len(self.lines(width)))

    def render(self) -> RenderableType:
        key_style = self.get_component_rich_style("hint-bar--key")
        description_style = self.get_component_rich_style("hint-bar--description")
        disabled_style = self.get_component_rich_style("hint-bar--disabled")
        text = Text(no_wrap=True, overflow="ellipsis")
        for number, line in enumerate(self.lines(self.content_size.width)):
            if number:
                text.append("\n")
            for index, (keys, description, enabled) in enumerate(line):
                if index:
                    text.append(self.SEPARATOR)
                text.append(keys, key_style if enabled else disabled_style)
                text.append(" ")
                text.append(description, description_style if enabled else disabled_style)
        return text
