"""Coding-agent background tasks: this host's own and other hosts' reports.

A hook records each background task as `<id>.json`; a push job marks it
finished and copies the host's list to other machines as `<host>.json`.
"""

from __future__ import annotations

import glob
import json
import os
import socket
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rich.text import Text

from fieldnotes.model import Detail, Item, Section, ago, now, parse_time, stamp
from fieldnotes.sources.base import Source

# A host that has not reported for this long is shown as silent.
SILENT_SECONDS = 90
OUTPUT_TAIL = 80


@dataclass
class Task:
    id: str
    host: str
    description: str
    cwd: str
    started: datetime | None
    finished: datetime | None
    silent: bool = False
    local: bool = False
    reported: datetime | None = None

    @property
    def state(self) -> str:
        if self.finished is not None:
            return "done"
        return "silent" if self.silent else "running"


def load_tasks(directory: Path, host: str, at: datetime) -> list[Task]:
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return []
    tasks = []
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            data = json.loads((directory / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        if isinstance(data.get("tasks"), list):
            reported = parse_time(data.get("updated"))
            silent = reported is None or (at - reported).total_seconds() > SILENT_SECONDS
            for record in data["tasks"]:
                if isinstance(record, dict):
                    tasks.append(
                        task(
                            record, data.get("host") or name.removesuffix(".json"), silent, reported
                        )
                    )
        elif data.get("id"):
            tasks.append(task(data, host, False, None, local=True))
    # Running tasks first, newest first within each group.
    tasks.sort(key=lambda t: (t.finished is not None, -(t.finished or t.started or at).timestamp()))
    return tasks


def task(record: dict, host: str, silent: bool, reported, local: bool = False) -> Task:
    return Task(
        id=str(record.get("id") or ""),
        host=host,
        description=record.get("description") or record.get("id") or "task",
        cwd=record.get("cwd") or "",
        started=parse_time(record.get("started")),
        finished=parse_time(record.get("finished")),
        silent=silent,
        local=local,
        reported=reported,
    )


def home_relative(path: str) -> str:
    home = str(Path.home())
    return "~" + path[len(home) :] if path == home or path.startswith(home + "/") else path


def output_file(task_id: str) -> Path | None:
    """The task's own output file, which Claude Code keeps under the temporary
    directory as `claude-<uid>/<project>/<session>/tasks/<id>.output`."""
    if not task_id or "/" in task_id:
        return None
    roots = {tempfile.gettempdir(), "/tmp"}
    for root in roots:
        for match in glob.glob(
            f"{glob.escape(root)}/claude-*/*/*/tasks/{glob.escape(task_id)}.output"
        ):
            return Path(match)
    return None


def tail(path: Path, lines: int) -> list[str]:
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        handle.seek(max(0, handle.tell() - 64 * 1024))
        return handle.read().decode("utf-8", "replace").splitlines()[-lines:]


class TasksSource(Source):
    name = "tasks"
    title = "Background tasks"
    empty = "No background tasks reported."
    interval = 2.0

    def fetch(self) -> list[Task]:
        return load_tasks(self.settings.tasks_dir, socket.gethostname(), now())

    def heading(self, data: list[Task]) -> str:
        running = sum(task.state == "running" for task in data)
        return f"{self.title} / {running} running"

    def items(self, data: list[Task]) -> list[Item]:
        at = now()
        rows = []
        for task in data:
            state = task.state
            if state == "done":
                status = f"done {ago(task.finished, at)} ago"
            elif state == "silent":
                status = "host silent"
            else:
                status = f"running {ago(task.started, at)}"
            rows.append(
                Item(
                    key=f"{task.host}/{task.id}",
                    title=task.description,
                    state=state,
                    status=status,
                    lines=(f"{task.host} · {home_relative(task.cwd)}",),
                    value=task.cwd or None,
                    value_label="directory",
                    data=task,
                )
            )
        return rows

    def summary(self, data: list[Task]) -> tuple[str, str] | None:
        running = sum(task.state == "running" for task in data)
        return (f"{running} running", "running") if running else None

    def detail(self, item: Item) -> Detail:
        task: Task = item.data
        at = now()
        if task.finished and task.started:
            ended = f"{stamp(task.finished, at)}, after {ago(task.started, task.finished)}"
        else:
            ended = "still running" if task.state == "running" else "—"
        fields = [
            ("Task", task.description),
            ("ID", task.id or "—"),
            ("Host", task.host),
            ("Directory", task.cwd or "—"),
            ("Started", stamp(task.started, at)),
            ("Finished", ended),
        ]
        if not task.local:
            fields.append(("Reported", stamp(task.reported, at)))
        path = output_file(task.id) if task.local else None
        if path is not None:
            try:
                output = Text.from_ansi("\n".join(tail(path, OUTPUT_TAIL)))
            except OSError as error:
                output = Text(f"Unreadable: {error}", "yellow")
            section = Section(
                f"Output (last {OUTPUT_TAIL} lines of {home_relative(str(path))})", output
            )
        elif task.local:
            section = Section("Output", Text("Its output file is gone.", "dim"))
        else:
            section = Section("Output", Text(f"The output stays on {task.host}.", "dim"))
        return Detail(task.description, fields, [section])
