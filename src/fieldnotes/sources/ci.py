"""GitHub Actions results, reduced to what needs attention."""

from __future__ import annotations

import base64
import os
import re
from dataclasses import dataclass, field
from datetime import datetime

from rich.text import Text

from fieldnotes.model import (
    Detail,
    Item,
    Section,
    SourceError,
    ago,
    capture,
    capture_json,
    now,
    parse_time,
    span,
    stamp,
)
from fieldnotes.sources.base import Source

# Lines kept from each failed step's log; `o` opens the whole log.
LOG_TAIL = 15
LOG_TIMESTAMP = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]+Z ?")
# Substitution and download progress, which buries a Nix build's error.
LOG_NOISE = re.compile(
    r"^\s*(copying path '|downloading '|these \d+ paths? will be (fetched|built)|"
    r"this path will be (fetched|built)|/nix/store/\S+$)"
)


@dataclass
class Run:
    id: int
    workflow: str
    title: str
    status: str
    conclusion: str | None
    created: datetime | None
    branch: str
    sha: str
    path: str
    url: str
    number: int = 0
    attempt: int = 1
    event: str = ""
    retired: bool = False

    @classmethod
    def from_api(cls, run: dict, active_paths: set[str]) -> Run:
        return cls(
            id=run.get("id") or 0,
            workflow=run.get("name") or "workflow",
            title=run.get("display_title") or "",
            status=run.get("status") or "",
            conclusion=run.get("conclusion"),
            created=parse_time(run.get("created_at")),
            branch=run.get("head_branch") or "",
            sha=run.get("head_sha") or "",
            path=run.get("path") or "",
            url=run.get("html_url") or "",
            number=run.get("run_number") or 0,
            attempt=run.get("run_attempt") or 1,
            event=run.get("event") or "",
            # GitHub stops listing a workflow once its file leaves the default
            # branch, but keeps showing that workflow's old runs.
            retired=(run.get("path") or "") not in active_paths,
        )

    @property
    def state(self) -> str:
        if self.status != "completed":
            return "running"
        if self.conclusion == "success":
            return "passed"
        if self.conclusion in ("cancelled", "skipped", "neutral"):
            return "skipped"
        return "failed"


@dataclass
class Verdict:
    """A workflow's standing on main."""

    workflow: str
    state: str
    latest: Run
    streak: int
    streak_open: bool
    """Every fetched run failed, so the streak may be longer."""
    about: str = ""


@dataclass
class CIState:
    main: list[Verdict] = field(default_factory=list)
    branches: list[Run] = field(default_factory=list)
    """The latest failing or running run of each other branch."""


def summarize(runs: list[Run], branches_alive: set[str] | None = None) -> CIState:
    """Reduce newest-first runs to each workflow's verdict on main and the
    failing or running runs of other branches.

    Superseded (cancelled) runs are ignored, since a newer push replaced them,
    and so are runs on branches that have since been deleted, which nobody can
    fix any more. `branches_alive` of `None` keeps every branch.
    """
    runs = [run for run in runs if run.state != "skipped"]
    history: dict[str, list[Run]] = {}
    branches: dict[tuple[str, str], Run] = {}
    for run in runs:
        if run.branch == "main":
            history.setdefault(run.workflow, []).append(run)
        else:
            branches.setdefault((run.workflow, run.branch), run)
    main = []
    for workflow, main_runs in history.items():
        latest = main_runs[0]
        streak = 0
        for run in main_runs:
            if run.state != "failed":
                break
            streak += 1
        main.append(
            Verdict(
                workflow=workflow,
                # A workflow removed from main never runs again, so its last
                # verdict is history rather than something to fix.
                state="retired" if latest.retired else latest.state,
                latest=latest,
                streak=streak,
                streak_open=streak == len(main_runs),
            )
        )
    order = {"failed": 0, "running": 1, "passed": 2}
    main.sort(key=lambda verdict: order.get(verdict.state, 3))
    others = [
        run
        for run in branches.values()
        if run.state in ("failed", "running")
        and not run.retired
        and (branches_alive is None or run.branch in branches_alive)
    ]
    return CIState(main, others)


def workflow_header(text: str) -> str:
    """The first sentence of a workflow file's top-level header comment."""
    comment: list[str] = []
    for line in text.splitlines():
        if line.startswith("#"):
            comment.append(line.lstrip("#").strip())
        elif comment or line.startswith("jobs:"):
            break
    return " ".join(comment).split(". ")[0].rstrip(".")


def verdict_label(verdict: Verdict, at: datetime) -> str:
    if verdict.state == "retired":
        return "retired"
    if verdict.state == "failed" and verdict.streak > 1:
        return f"failing ×{verdict.streak}{'+' if verdict.streak_open else ''}"
    if verdict.state == "running":
        return f"running {ago(verdict.latest.created, at)}"
    return f"{verdict.state} {ago(verdict.latest.created, at)} ago"


def failed_logs(log: str) -> list[tuple[str, str, list[str]]]:
    """Group `gh run view --log-failed` output by job and step, keeping the
    lines that lead up to each one's last error."""
    groups: dict[tuple[str, str], list[str]] = {}
    for line in log.splitlines():
        job, step, rest = (line.split("\t", 2) + ["", ""])[:3]
        groups.setdefault((job, step), []).append(LOG_TIMESTAMP.sub("", rest))
    return [(job, step, excerpt(lines)) for (job, step), lines in groups.items()]


def excerpt(lines: list[str]) -> list[str]:
    """Up to `LOG_TAIL` lines ending at the last `##[error]` marker, since the
    end of a job's log is usually cleanup; the tail when there is no marker."""
    lines = [line for line in lines if not LOG_NOISE.match(line)]
    errors = [index for index, line in enumerate(lines) if "##[error]" in line]
    end = errors[-1] + 1 if errors else len(lines)
    return lines[max(0, end - LOG_TAIL) : end]


class CISource(Source):
    name = "ci"
    title = "CI"
    empty = "No recent workflow runs."

    def __init__(self, settings) -> None:
        super().__init__(settings)
        # Workflow path -> (commit it was read at, header sentence).
        self._about: dict[str, tuple[str, str]] = {}

    @property
    def repo(self) -> str:
        return self.settings.ci_repo

    def fetch(self) -> CIState:
        data = capture_json(["gh", "api", f"repos/{self.repo}/actions/runs?per_page=40"])
        workflows = capture_json(["gh", "api", f"repos/{self.repo}/actions/workflows"])
        active = {
            workflow.get("path")
            for workflow in (workflows or {}).get("workflows", [])
            if workflow.get("state") == "active"
        }
        runs = [Run.from_api(run, active) for run in (data or {}).get("workflow_runs", [])]
        state = summarize(runs, self.branches())
        for verdict in state.main:
            verdict.about = self.about(verdict.latest.path, verdict.latest.sha or "main")
        return state

    def branches(self) -> set[str] | None:
        """The repository's branches, or `None` when GitHub will not say."""
        try:
            names = capture(
                [
                    "gh",
                    "api",
                    "--paginate",
                    f"repos/{self.repo}/branches?per_page=100",
                    "--jq",
                    ".[].name",
                ]
            )
        except SourceError:
            return None
        return set(names.split())

    def about(self, path: str, sha: str) -> str:
        # Dependabot and Copilot runs have no file in the repository.
        if not path.startswith(".github/workflows/"):
            return "managed by GitHub"
        cached = self._about.get(path)
        if cached and cached[0] == sha:
            return cached[1]
        try:
            data = capture_json(["gh", "api", f"repos/{self.repo}/contents/{path}?ref={sha}"])
            text = base64.b64decode(data.get("content", "")).decode()
        except (SourceError, ValueError, AttributeError):
            return cached[1] if cached else ""
        about = workflow_header(text)
        self._about[path] = (sha, about)
        return about

    def heading(self, data: CIState) -> str:
        return f"CI / {self.repo}"

    def items(self, data: CIState) -> list[Item]:
        at = now()
        rows = []
        for verdict in data.main:
            run = verdict.latest
            source = os.path.basename(run.path) or run.path
            about = f"{verdict.about} · {source}" if verdict.about else source
            if verdict.state == "retired":
                last = f"Removed from main; last run {run.state} {ago(run.created, at)} ago"
            else:
                last = f"↳ {run.title} · {ago(run.created, at)} ago"
            rows.append(
                Item(
                    key=f"main/{verdict.workflow}",
                    title=f"{verdict.workflow} on main",
                    state=verdict.state,
                    status=verdict_label(verdict, at),
                    lines=(about, last),
                    url=run.url or None,
                    value=run.path or None,
                    value_label="workflow file",
                    data=verdict,
                )
            )
        for run in data.branches:
            rows.append(
                Item(
                    key=f"{run.branch}/{run.workflow}",
                    title=f"{run.workflow} on {run.branch}",
                    state=run.state,
                    status=f"{run.state} {ago(run.created, at)}"
                    + ("" if run.state == "running" else " ago"),
                    lines=(f"↳ {run.title}",),
                    url=run.url or None,
                    value=run.branch,
                    value_label="branch",
                    data=run,
                )
            )
        return rows

    def summary(self, data: CIState) -> tuple[str, str] | None:
        states = [verdict.state for verdict in data.main] + [run.state for run in data.branches]
        failing = states.count("failed")
        if failing:
            return f"CI {failing} failing", "failed"
        if "running" in states:
            return "CI running", "running"
        return ("CI passing", "passed") if states else None

    @staticmethod
    def run_of(item: Item) -> Run:
        return item.data.latest if isinstance(item.data, Verdict) else item.data

    def can_rerun(self, item: Item) -> bool:
        run = self.run_of(item)
        return isinstance(run, Run) and run.state == "failed" and bool(run.id)

    def rerun(self, item: Item) -> str:
        run = self.run_of(item)
        capture(["gh", "run", "rerun", str(run.id), "--failed", "-R", self.repo], timeout=30)
        return f"Rerunning the failed jobs of {run.workflow} #{run.number}"

    def detail(self, item: Item) -> Detail:
        at = now()
        run = self.run_of(item)
        fields = [
            ("Workflow", run.workflow),
            ("File", run.path or "—"),
        ]
        if isinstance(item.data, Verdict):
            fields.append(("About", item.data.about or "—"))
            fields.append(("On main", verdict_label(item.data, at)))
        fields += [
            ("Branch", run.branch),
            ("Commit", run.sha[:12] or "—"),
            ("Title", run.title or "—"),
            ("Run", f"#{run.number}, attempt {run.attempt}, from {run.event or 'unknown'}"),
            ("Started", stamp(run.created, at)),
            ("Result", run.conclusion or run.status or "—"),
            ("URL", run.url or "—"),
        ]
        sections = [self.jobs_section(run)]
        if run.state == "failed":
            sections.append(self.log_section(run))
        return Detail(item.title, fields, sections)

    def jobs_section(self, run: Run) -> Section:
        try:
            data = capture_json(
                ["gh", "api", f"repos/{self.repo}/actions/runs/{run.id}/jobs?per_page=100"]
            )
        except SourceError as error:
            return Section("Jobs", Text(f"Unavailable: {error}", "yellow"))
        jobs = data.get("jobs", []) if isinstance(data, dict) else []
        order = {"failure": 0, "timed_out": 0, "startup_failure": 0, None: 1}
        jobs.sort(key=lambda job: order.get(job.get("conclusion"), 2))
        text = Text()
        quiet: dict[str, int] = {}
        for job in jobs:
            conclusion = job.get("conclusion")
            if conclusion in ("success", "skipped", "neutral"):
                quiet[conclusion] = quiet.get(conclusion, 0) + 1
                continue
            started = parse_time(job.get("started_at"))
            completed = parse_time(job.get("completed_at"))
            took = (
                span((completed - started).total_seconds()) if started and completed else "running"
            )
            marker, style = ("●", "yellow") if conclusion is None else ("✗", "red")
            text.append(f"{marker} {job.get('name')}", style)
            text.append(f"  {took}\n", "dim")
            for step in job.get("steps", []):
                if step.get("conclusion") == "failure":
                    text.append(f"    ↳ {step.get('name')}\n", "red")
        if quiet:
            text.append(
                ", ".join(f"{count} {name}" for name, count in sorted(quiet.items())), "dim"
            )
        return Section("Jobs", text if text else Text("No jobs reported.", "dim"))

    def log_section(self, run: Run) -> Section:
        try:
            log = capture(
                ["gh", "run", "view", str(run.id), "-R", self.repo, "--log-failed"],
                timeout=45,
            )
        except SourceError as error:
            return Section("Failed steps", Text(f"Log unavailable: {error}", "yellow"))
        text = Text()
        for job, step, lines in failed_logs(log):
            text.append(f"{job} ▸ {step}\n", "bold red")
            for line in lines:
                rendered = Text.from_ansi(line)
                if "##[error]" in line or "error" in line.lower():
                    rendered.stylize("red")
                text.append_text(rendered)
                text.append("\n")
            text.append("\n")
        return Section(
            f"Failed steps (up to {LOG_TAIL} lines before each last error)",
            text if text else Text("GitHub returned no failed-step log.", "dim"),
        )
