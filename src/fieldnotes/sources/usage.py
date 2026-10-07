"""Provider usage windows, read from ai-usagebar."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from fieldnotes.model import (
    Detail,
    Item,
    capture_json,
    now,
    parse_time,
    span,
    stamp,
    until,
)
from fieldnotes.sources.base import Source

# A window this many percentage points ahead of (or behind) an even pace
# through its period is off track.
PACE_TOLERANCE = 10


@dataclass
class Window:
    provider: str
    plan: str
    label: str
    value: str
    percent: int
    reset: datetime | None
    window_secs: float | None
    note: str
    fetched: datetime | None

    def elapsed(self, at: datetime) -> float | None:
        """How far through its period the window is, in percent."""
        if not self.window_secs or self.reset is None:
            return None
        left = (self.reset - at).total_seconds()
        return max(0.0, min(100.0, 100 * (self.window_secs - left) / self.window_secs))

    def pace(self, at: datetime) -> float | None:
        """Points used ahead of (positive) or behind an even pace."""
        elapsed = self.elapsed(at)
        return None if elapsed is None else self.percent - elapsed


@dataclass
class Problem:
    provider: str
    problem: str
    error: str


def provider_problem(error: object) -> str:
    text = str(error or "unavailable")
    if "credentials.json" in text or "No such file" in text:
        return "not signed in"
    if "no API key" in text:
        return "no API key"
    return text.splitlines()[0]


def parse(data: object) -> list[Window | Problem]:
    entries = data.get("entries", []) if isinstance(data, dict) else []
    windows: list[Window | Problem] = []
    problems: list[Window | Problem] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        provider = entry.get("display_name") or entry.get("id") or "Provider"
        if entry.get("status") != "ready":
            error = str(entry.get("error") or "unavailable")
            problems.append(Problem(provider, provider_problem(error), error))
            continue
        for metric in entry.get("metrics", []):
            percent = metric.get("percent")
            if percent is None:
                continue
            window_secs = metric.get("window_secs")
            windows.append(
                Window(
                    provider=provider,
                    plan=entry.get("plan") or "",
                    label=metric.get("label") or "Usage",
                    value=metric.get("value") or f"{percent}%",
                    percent=max(0, min(100, int(percent))),
                    reset=parse_time(metric.get("reset_at")),
                    window_secs=window_secs if isinstance(window_secs, (int, float)) else None,
                    note=metric.get("detail") or "",
                    fetched=parse_time(entry.get("fetched_at")),
                )
            )
    return windows + problems


def pace_state(pace: float | None) -> str:
    if pace is None:
        return "info"
    if pace >= PACE_TOLERANCE:
        return "over"
    if pace <= -PACE_TOLERANCE:
        return "under"
    return "on-track"


def pace_label(pace: float | None) -> str:
    if pace is None:
        return ""
    if pace >= PACE_TOLERANCE:
        return f"{round(pace)} pts ahead"
    if pace <= -PACE_TOLERANCE:
        return f"{round(-pace)} pts behind"
    return "on track"


class UsageSource(Source):
    name = "usage"
    title = "Provider windows"
    empty = "No active provider windows reported."

    def fetch(self) -> list[Window | Problem]:
        return parse(capture_json(["ai-usagebar", "usage", "--json"]))

    def items(self, data: list[Window | Problem]) -> list[Item]:
        at = now()
        rows = []
        for entry in data:
            if isinstance(entry, Problem):
                rows.append(
                    Item(
                        key=f"{entry.provider}/problem",
                        title=entry.provider,
                        state="warn",
                        status=entry.problem,
                        value=entry.error,
                        value_label="error",
                        data=entry,
                    )
                )
                continue
            pace = entry.pace(at)
            resets = f"resets in {until(entry.reset, at)}" if entry.reset else ""
            rows.append(
                Item(
                    key=f"{entry.provider}/{entry.label}",
                    title=f"{entry.provider} / {entry.label}",
                    state=pace_state(pace),
                    status=f"{entry.percent}%",
                    meter=entry.percent,
                    lines=(" · ".join(part for part in (pace_label(pace), resets) if part),),
                    value=f"{entry.provider} {entry.label}: {entry.value}",
                    value_label="usage",
                    data=entry,
                )
            )
        return rows

    def summary(self, data: list[Window | Problem]) -> tuple[str, str] | None:
        at = now()
        over = sum(
            isinstance(entry, Window) and pace_state(entry.pace(at)) == "over" for entry in data
        )
        if over:
            return f"usage {over} over pace", "over"
        return None

    def detail(self, item: Item) -> Detail:
        entry = item.data
        if isinstance(entry, Problem):
            return Detail(
                entry.provider,
                [("Provider", entry.provider), ("Problem", entry.problem), ("Error", entry.error)],
            )
        at = now()
        elapsed = entry.elapsed(at)
        fields = [
            ("Provider", entry.provider),
            ("Plan", entry.plan or "—"),
            ("Window", entry.label),
            ("Used", f"{entry.value} ({entry.percent}%)"),
            ("Elapsed", "—" if elapsed is None else f"{elapsed:.0f}% of the window"),
            ("Pace", pace_label(entry.pace(at)) or "—"),
            (
                "Resets",
                f"{entry.reset:%a %d %b %H:%M} · in {until(entry.reset, at)}"
                if entry.reset
                else "—",
            ),
            ("Length", span(entry.window_secs) if entry.window_secs else "—"),
            ("Reported", entry.note or "—"),
            ("Fetched", stamp(entry.fetched, at)),
        ]
        return Detail(item.title, fields)
