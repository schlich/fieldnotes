"""Provider usage windows, read from ai-usagebar and Jev's usage ledger."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from fieldnotes.model import (
    Detail,
    Item,
    SourceError,
    ago,
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

# Trailing windows summed from Jev's ledger. TypeSafe reports usage per
# response only, with no plan limit, so these count tokens rather than
# measure a share of a quota.
LEDGER_WINDOWS = (("24h", timedelta(days=1)), ("7d", timedelta(days=7)))


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


@dataclass
class Ledger:
    """Token usage a local ledger recorded over one trailing window."""

    provider: str
    label: str
    window_secs: float
    requests: int
    input_tokens: int
    output_tokens: int
    limited: int
    """Responses refused for the rate limit (HTTP 429)."""
    failed: int
    """Other non-200 responses."""
    last: datetime | None
    path: Path

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def count(value: int) -> str:
    """A compact count: `950`, `12.3k`, `4.1M`."""
    if value < 1000:
        return str(value)
    if value < 1_000_000:
        return f"{value / 1000:.1f}k"
    return f"{value / 1_000_000:.1f}M"


def read_ledger(path: Path, at: datetime) -> list[dict] | None:
    """The ledger's entries from the longest window, or `None` without one.

    jev appends one JSON object per response: `at`, `command`, `model`,
    `status`, `input_tokens`, `output_tokens`.
    """
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise SourceError(f"cannot read {path}: {error.strerror}") from None
    oldest = at - max(length for _, length in LEDGER_WINDOWS)
    entries = []
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        moment = parse_time(entry.get("at"))
        if moment is None or moment < oldest:
            continue
        entries.append({**entry, "at": moment})
    return entries


def tokens_of(entry: dict, name: str) -> int:
    value = entry.get(name)
    return value if isinstance(value, int) else 0


def ledger_windows(provider: str, path: Path, entries: list[dict], at: datetime) -> list[Ledger]:
    windows = []
    for label, length in LEDGER_WINDOWS:
        recent = [entry for entry in entries if entry["at"] >= at - length]
        statuses = [entry.get("status") for entry in recent]
        windows.append(
            Ledger(
                provider=provider,
                label=label,
                window_secs=length.total_seconds(),
                requests=len(recent),
                input_tokens=sum(tokens_of(entry, "input_tokens") for entry in recent),
                output_tokens=sum(tokens_of(entry, "output_tokens") for entry in recent),
                limited=statuses.count(429),
                failed=sum(status not in (200, 429) for status in statuses),
                last=max((entry["at"] for entry in recent), default=None),
                path=path,
            )
        )
    return windows


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

    def fetch(self) -> list[Window | Ledger | Problem]:
        at = now()
        path = self.settings.jev_log
        entries = read_ledger(path, at)
        ledgers = [] if entries is None else ledger_windows("Jev", path, entries, at)
        try:
            reported = parse(capture_json(["ai-usagebar", "usage", "--json"]))
        except SourceError as error:
            # Without Jev rows, the panel itself shows the error.
            if not ledgers:
                raise
            reported = [Problem("ai-usagebar", provider_problem(error), str(error))]
        windows = [entry for entry in reported if isinstance(entry, Window)]
        problems = [entry for entry in reported if isinstance(entry, Problem)]
        return [*windows, *ledgers, *problems]

    def items(self, data: list[Window | Ledger | Problem]) -> list[Item]:
        at = now()
        rows = []
        for entry in data:
            if isinstance(entry, Ledger):
                refused = [
                    f"{entry.limited} rate-limited" if entry.limited else "",
                    f"{entry.failed} failed" if entry.failed else "",
                ]
                rows.append(
                    Item(
                        key=f"{entry.provider}/{entry.label}",
                        title=f"{entry.provider} / {entry.label}",
                        state="warn" if entry.limited or entry.failed else "info",
                        status=f"{count(entry.tokens)} tok",
                        lines=(
                            " · ".join(
                                part
                                for part in (
                                    f"{entry.requests} requests",
                                    *refused,
                                    f"last {ago(entry.last, at)} ago" if entry.last else "",
                                )
                                if part
                            ),
                        ),
                        value=f"{entry.provider} {entry.label}: {entry.tokens} tokens",
                        value_label="usage",
                        data=entry,
                    )
                )
                continue
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

    def summary(self, data: list[Window | Ledger | Problem]) -> tuple[str, str] | None:
        at = now()
        over = sum(
            isinstance(entry, Window) and pace_state(entry.pace(at)) == "over" for entry in data
        )
        if over:
            return f"usage {over} over pace", "over"
        limited = next(
            (entry for entry in data if isinstance(entry, Ledger) and entry.limited), None
        )
        if limited:
            return f"{limited.provider} rate-limited {limited.limited}× in {limited.label}", "warn"
        return None

    def detail(self, item: Item) -> Detail:
        entry = item.data
        if isinstance(entry, Ledger):
            at = now()
            return Detail(
                item.title,
                [
                    ("Provider", entry.provider),
                    ("Window", f"trailing {span(entry.window_secs)}"),
                    ("Requests", str(entry.requests)),
                    (
                        "Tokens",
                        f"{entry.tokens:,} ({entry.input_tokens:,} in, {entry.output_tokens:,} out)",
                    ),
                    ("Rate-limited", str(entry.limited)),
                    ("Failed", str(entry.failed)),
                    ("Last request", stamp(entry.last, at)),
                    ("Ledger", str(entry.path)),
                    ("Limit", "TypeSafe reports no plan limit, so there is no meter"),
                ],
            )
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
