"""Synthetic data shaped like each tool's real output, and an app built on it."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest

from fieldnotes.app import FieldnotesApp
from fieldnotes.config import Settings
from fieldnotes.model import now
from fieldnotes.screens import DashboardScreen
from fieldnotes.sources.ci import CISource, Run, summarize
from fieldnotes.sources.librarian import LibrarianSource, Note
from fieldnotes.sources.tasks import TasksSource, load_tasks
from fieldnotes.sources.topics import TopicsSource, pending_topics
from fieldnotes.sources.usage import UsageSource, parse


def iso(delta: timedelta) -> str:
    return (now() + delta).isoformat()


def usage_json() -> dict:
    """`ai-usagebar usage --json`: one window well ahead of pace, one behind,
    one on track, and a provider without credentials."""
    return {
        "schema_version": 1,
        "entries": [
            {
                "id": "anthropic",
                "display_name": "Claude",
                "plan": "Pro",
                "status": "ready",
                "fetched_at": iso(timedelta(seconds=-30)),
                "metrics": [
                    # 2h into 5h is 40% elapsed; 70% used is 30 points ahead.
                    {
                        "label": "Session (5h)",
                        "percent": 70,
                        "value": "70%",
                        "reset_at": iso(timedelta(hours=3)),
                        "window_secs": 5 * 3600,
                        "detail": "Resets in 3h",
                    },
                    # Half way through the week with 20% used: 30 points behind.
                    {
                        "label": "Weekly (7d)",
                        "percent": 20,
                        "value": "20%",
                        "reset_at": iso(timedelta(days=3.5)),
                        "window_secs": 7 * 86400,
                    },
                    {"label": "No percent", "value": "n/a"},
                ],
            },
            {
                "id": "openai",
                "display_name": "Codex",
                "status": "ready",
                "metrics": [
                    {
                        "label": "Codex 5h",
                        "percent": 52,
                        "value": "52%",
                        "reset_at": iso(timedelta(hours=2.5)),
                        "window_secs": 5 * 3600,
                    }
                ],
            },
            {
                "id": "zai",
                "display_name": "Z.AI",
                "status": "error",
                "error": "credentials error: Zai: no API key. Either set one",
            },
        ],
    }


def make_run(
    run_id: int,
    workflow: str,
    branch: str,
    conclusion: str | None,
    hours_ago: float,
    *,
    path: str = ".github/workflows/nix-ci.yml",
    retired: bool = False,
) -> Run:
    return Run(
        id=run_id,
        workflow=workflow,
        title=f"Commit for run {run_id}",
        status="completed" if conclusion else "in_progress",
        conclusion=conclusion,
        created=now() - timedelta(hours=hours_ago),
        branch=branch,
        sha=f"{run_id:040x}",
        path=path,
        url=f"https://github.com/example/repo/actions/runs/{run_id}",
        number=run_id,
        retired=retired,
    )


def ci_runs() -> list[Run]:
    """Newest first, as GitHub lists them."""
    return [
        make_run(10, "nix-ci", "main", "failure", 1),
        make_run(9, "nix-ci", "jj-add-a-panel-qwertyzk", "failure", 2),
        make_run(8, "nix-ci", "main", "cancelled", 3),
        make_run(7, "nix-ci", "main", "failure", 4),
        make_run(6, "release", "main", "success", 5, path=".github/workflows/release.yml"),
        make_run(5, "nix-ci", "deleted-branch", "failure", 6),
        make_run(4, "nix-ci", "main", "success", 7),
        make_run(
            3, "old-lint", "main", "failure", 30, path=".github/workflows/lint.yml", retired=True
        ),
        make_run(2, "nix-ci", "jj-busy-topic-mnopqrst", None, 0.1),
    ]


def ci_state():
    state = summarize(ci_runs(), {"main", "jj-add-a-panel-qwertyzk", "jj-busy-topic-mnopqrst"})
    for verdict in state.main:
        verdict.about = f"What {verdict.workflow} does"
    return state


def knot_hash(byte: int) -> list[int]:
    return [byte] * 20


def knot_branches() -> list[dict]:
    """`sh.tangled.repo.branches`."""

    def branch(name: str, byte: int, message: str, days_ago: float) -> dict:
        when = iso(timedelta(days=-days_ago))
        return {
            "reference": {"name": name, "hash": bytes(knot_hash(byte)).hex()},
            "commit": {
                "Message": message,
                "Author": {"When": when},
                "Committer": {"When": when},
            },
        }

    return [
        branch("main", 1, "Trunk", 0),
        branch("jj-landed-as-is-aaaaaaaa", 2, "Landed as is\n\nImpact: refactor", 4),
        branch("jj-landed-after-rebase-bbbbbbbb", 3, "Landed after a rebase", 4),
        branch("jj-stale-topic-kkkkkkkk", 4, "Stale topic\n\nWhy it waits.\n\nImpact: behavior", 3),
        branch("jj-fresh-topic-llllllll", 5, "Fresh topic", 0.1),
        branch("wip", 6, "Not a topic", 1),
    ]


def knot_main_log() -> list[dict]:
    """`sh.tangled.repo.log` for main: the first topic landed as is, the
    second under a new hash after `ci land` rebased it."""
    return [
        {"hash": knot_hash(2), "message": "Landed as is\n\nImpact: refactor"},
        {"hash": knot_hash(9), "message": "Landed after a rebase"},
    ]


def notes() -> list[Note]:
    at = now()
    return [
        Note(
            "decisions/use-a-grid",
            "Use a grid",
            "decision",
            at - timedelta(hours=2),
            "# Use a grid\n\nPanels sit in a grid.",
        ),
        Note(
            "gotchas/stale-cache",
            "A stale cache",
            "gotcha",
            at - timedelta(days=1),
            "- Clear it first.",
        ),
    ]


class Fake:
    """Replaces a source's blocking calls with canned answers."""

    def __init__(self, source, data=None, error: Exception | None = None):
        self.source = source
        self.reruns: list[str] = []
        source.fetch = self.fetch
        source.rerun = self.rerun
        self.data, self.error = data, error

    def fetch(self):
        if self.error:
            raise self.error
        return self.data

    def rerun(self, item):
        self.reruns.append(item.key)
        return f"Rerunning {item.key}"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    (tmp_path / "tasks").mkdir()
    (tmp_path / "kb").mkdir()
    return Settings(
        ci_repo="example/repo",
        tangled_repo_url="https://tangled.example/@someone/repo",
        tasks_dir=tmp_path / "tasks",
        iwe_root=tmp_path / "kb",
    )


@pytest.fixture
def fakes(settings: Settings) -> dict[str, Fake]:
    at = now()
    return {
        "usage": Fake(UsageSource(settings), parse(usage_json())),
        "ci": Fake(CISource(settings), ci_state()),
        "topics": Fake(TopicsSource(settings), pending_topics(knot_branches(), knot_main_log())),
        "tasks": Fake(TasksSource(settings), load_tasks(settings.tasks_dir, "here", at)),
        "librarian": Fake(LibrarianSource(settings), notes()),
    }


@pytest.fixture
def app(settings: Settings, fakes: dict[str, Fake]) -> FieldnotesApp:
    return FieldnotesApp(settings, [fake.source for fake in fakes.values()])


async def settle(pilot, app: FieldnotesApp) -> DashboardScreen:
    """Wait until every source has answered once."""
    dashboard = app.dashboard
    for _ in range(100):
        await pilot.pause(0.02)
        if all(state.updated or state.error for state in dashboard.states.values()):
            break
    await pilot.pause()
    return dashboard


def run(scenario) -> None:
    asyncio.run(scenario())
