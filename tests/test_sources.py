import json
from datetime import timedelta
from pathlib import Path

import pytest
from conftest import ci_runs, ci_state, knot_branches, knot_main_log, usage_json

from fieldnotes.model import SourceError, now
from fieldnotes.sources.ci import CISource, excerpt, failed_logs, summarize, workflow_header
from fieldnotes.sources.librarian import (
    LibrarianSource,
    Note,
    split_frontmatter,
)
from fieldnotes.sources.librarian import (
    parse as parse_notes,
)
from fieldnotes.sources.tasks import TasksSource, home_relative, load_tasks
from fieldnotes.sources.topics import TopicsSource, pending_topics
from fieldnotes.sources.usage import (
    Ledger,
    Problem,
    UsageSource,
    Window,
    count,
    ledger_windows,
    pace_label,
    parse,
    read_ledger,
)

# Usage


def test_usage_keeps_windows_with_a_percentage_and_lists_problems_last():
    entries = parse(usage_json())
    assert [type(entry) for entry in entries] == [Window, Window, Window, Problem]
    assert entries[-1].problem == "no API key"


def test_usage_rows_are_colored_by_pace_not_by_amount_used(settings):
    source = UsageSource(settings)
    rows = {row.key: row for row in source.items(parse(usage_json()))}
    assert rows["Claude/Session (5h)"].state == "over"
    assert rows["Claude/Session (5h)"].lines[0].startswith("30 pts ahead · resets in 2h 5")
    assert rows["Claude/Weekly (7d)"].state == "under"
    assert rows["Codex/Codex 5h"].state == "on-track"
    assert rows["Z.AI/problem"].status == "no API key"
    assert source.summary(parse(usage_json())) == ("usage 1 over pace", "over")


def test_pace_label_names_the_direction():
    assert pace_label(None) == ""
    assert pace_label(3) == "on track"
    assert pace_label(12.4) == "12 pts ahead"
    assert pace_label(-30) == "30 pts behind"


def write_ledger(path: Path) -> None:
    """jev's ledger: two answers today, a 429 today, one answer three days
    ago, one too old to count, and a torn line."""

    def entry(hours_ago: float, status: int, tokens: tuple[int, int]) -> str:
        return json.dumps(
            {
                "at": (now() - timedelta(hours=hours_ago)).isoformat(),
                "command": "bash-guard",
                "model": "jev-1",
                "status": status,
                "input_tokens": tokens[0],
                "output_tokens": tokens[1],
            }
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        entry(1, 200, (296, 20)),
        entry(2, 200, (1000, 40)),
        entry(3, 429, (0, 0)),
        entry(72, 200, (5000, 100)),
        entry(24 * 9, 200, (99999, 1)),
        '{"at": "2026-10-',
    ]
    path.write_text("\n".join(lines) + "\n")


def test_ledger_windows_sum_tokens_and_count_refusals(tmp_path):
    path = tmp_path / "usage.jsonl"
    write_ledger(path)
    at = now()
    day, week = ledger_windows("Jev", path, read_ledger(path, at), at)
    assert (day.label, day.requests, day.tokens, day.limited, day.failed) == ("24h", 3, 1356, 1, 0)
    assert (week.label, week.requests, week.input_tokens, week.output_tokens) == (
        "7d",
        4,
        6296,
        160,
    )
    assert read_ledger(tmp_path / "missing.jsonl", at) is None


def test_count_is_compact():
    assert [count(n) for n in (950, 12_345, 4_100_000)] == ["950", "12.3k", "4.1M"]


def test_usage_adds_jev_rows_between_windows_and_problems(settings, monkeypatch):
    write_ledger(settings.jev_log)
    monkeypatch.setattr("fieldnotes.sources.usage.capture_json", lambda command, **_: usage_json())
    source = UsageSource(settings)
    data = source.fetch()
    assert [type(entry) for entry in data] == [Window] * 3 + [Ledger] * 2 + [Problem]
    rows = {row.key: row for row in source.items(data)}
    assert rows["Jev/24h"].status == "1.4k tok"
    assert rows["Jev/24h"].state == "warn"
    assert rows["Jev/24h"].lines[0].startswith("3 requests · 1 rate-limited · last ")
    assert rows["Jev/24h"].meter is None
    assert rows["Jev/7d"].status == "6.5k tok"
    assert source.detail(rows["Jev/7d"]).fields[3] == ("Tokens", "6,456 (6,296 in, 160 out)")


def test_usage_keeps_jev_rows_when_ai_usagebar_fails(settings, monkeypatch):
    def fail(command, **_):
        raise SourceError("ai-usagebar is not installed")

    monkeypatch.setattr("fieldnotes.sources.usage.capture_json", fail)
    source = UsageSource(settings)
    # Without a ledger, the panel itself shows the error.
    with pytest.raises(SourceError):
        source.fetch()

    write_ledger(settings.jev_log)
    data = source.fetch()
    assert [type(entry) for entry in data] == [Ledger, Ledger, Problem]
    assert data[-1].provider == "ai-usagebar"
    assert source.summary(data) == ("Jev rate-limited 1× in 24h", "warn")


# CI


def test_summarize_reports_failure_streaks_on_main_and_failing_branches():
    state = ci_state()
    by_workflow = {verdict.workflow: verdict for verdict in state.main}
    # The cancelled run between the two failures does not break the streak.
    assert by_workflow["nix-ci"].state == "failed"
    assert by_workflow["nix-ci"].streak == 2
    assert not by_workflow["nix-ci"].streak_open
    assert by_workflow["old-lint"].state == "retired"
    assert state.main[0].workflow == "nix-ci"
    branches = {run.branch for run in state.branches}
    # A run on a deleted branch is not something anyone can fix.
    assert branches == {"jj-add-a-panel-qwertyzk", "jj-busy-topic-mnopqrst"}


def test_summarize_keeps_every_branch_when_github_does_not_list_them():
    state = summarize(ci_runs(), None)
    assert "deleted-branch" in {run.branch for run in state.branches}


def test_ci_rows_link_to_runs_and_offer_reruns_only_for_failures(settings):
    source = CISource(settings)
    rows = {row.key: row for row in source.items(ci_state())}
    main = rows["main/nix-ci"]
    assert main.status == "failing ×2"
    assert main.url.endswith("/runs/10")
    assert main.value == ".github/workflows/nix-ci.yml"
    assert source.can_rerun(main)
    assert not source.can_rerun(rows["main/release"])
    busy = rows["jj-busy-topic-mnopqrst/nix-ci"]
    assert busy.status.startswith("running ")
    assert not source.can_rerun(busy)
    assert source.summary(ci_state()) == ("CI 2 failing", "failed")


def test_workflow_header_is_the_first_sentence_of_the_top_comment():
    text = "# Build every check. Then report.\n# More.\nname: ci\njobs:\n  x: {}\n"
    assert workflow_header(text) == "Build every check"
    assert workflow_header("name: ci\njobs:\n") == ""


def test_failed_logs_end_at_the_last_error_not_at_cleanup():
    lines = [f"build\tCompile\t2026-10-07T11:52:3{i}.1234567Z line {i}" for i in range(5)]
    lines.insert(3, "build\tCompile\t2026-10-07T11:52:33.0000000Z ##[error]boom")
    lines.append("build\tCompile\t2026-10-07T11:52:39.0000000Z Post job cleanup.")
    [(job, step, kept)] = failed_logs("\n".join(lines))
    assert (job, step) == ("build", "Compile")
    assert kept[-1] == "##[error]boom"
    assert "Post job cleanup." not in kept
    assert excerpt(["a", "b"]) == ["a", "b"]


def test_failed_logs_drop_nix_download_noise():
    lines = [
        "copying path '/nix/store/aaa-deadnix' from 'https://cache.example'...",
        "these 12 paths will be fetched (3.1 MiB download):",
        "  /nix/store/bbb-nixfmt-1.1.0",
        "ERRO file has changed path=README.md",
        "##[error]Process completed with exit code 1.",
    ]
    assert excerpt(lines) == lines[3:]


# Topics


def test_pending_topics_skip_landed_and_rebased_topics_and_other_branches():
    topics = pending_topics(knot_branches(), knot_main_log())
    assert [topic.branch for topic in topics] == [
        "jj-stale-topic-kkkkkkkk",
        "jj-fresh-topic-llllllll",
    ]
    stale = topics[0]
    assert stale.change_id == "kkkkkkkk"
    assert stale.stale(now())
    assert not topics[1].stale(now())


def test_topic_rows_open_the_comparison_and_copy_the_branch(settings):
    source = TopicsSource(settings)
    topics = pending_topics(knot_branches(), knot_main_log())
    [stale, fresh] = source.items(topics)
    assert stale.state == "stale" and fresh.state == "waiting"
    assert (
        stale.url == "https://tangled.example/@someone/repo/compare/main...jj-stale-topic-kkkkkkkk"
    )
    assert stale.value == "jj-stale-topic-kkkkkkkk"
    assert source.summary(topics) == ("2 waiting, 1 over 2d", "stale")
    detail = source.detail(stale)
    assert ("Change", "kkkkkkkk") in detail.fields
    assert "Why it waits." in str(detail.sections[0].body)


# Tasks


def write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def test_tasks_read_local_records_and_other_hosts_reports(settings):
    at = now()
    directory = settings.tasks_dir
    write(
        directory / "laptop.json",
        {
            "host": "laptop",
            "updated": (at - timedelta(seconds=10)).isoformat(),
            "tasks": [
                {
                    "id": "a",
                    "description": "build",
                    "cwd": "/w",
                    "started": (at - timedelta(minutes=5)).isoformat(),
                },
                {
                    "id": "b",
                    "description": "test",
                    "cwd": "/w",
                    "started": (at - timedelta(minutes=9)).isoformat(),
                    "finished": (at - timedelta(minutes=1)).isoformat(),
                },
            ],
        },
    )
    write(
        directory / "server.json",
        {
            "host": "server",
            "updated": (at - timedelta(minutes=10)).isoformat(),
            "tasks": [
                {
                    "id": "c",
                    "description": "deploy",
                    "started": (at - timedelta(hours=1)).isoformat(),
                }
            ],
        },
    )
    write(
        directory / "x1.json",
        {
            "id": "x1",
            "description": "watch-job",
            "cwd": str(Path.home() / "src"),
            "started": at.isoformat(),
            "finished": None,
        },
    )
    (directory / "broken.json").write_text("{", encoding="utf-8")
    tasks = load_tasks(directory, "here", at)
    assert [(task.host, task.id, task.state) for task in tasks] == [
        ("here", "x1", "running"),
        ("laptop", "a", "running"),
        ("server", "c", "silent"),
        ("laptop", "b", "done"),
    ]
    rows = TasksSource(settings).items(tasks)
    assert rows[0].lines == ("here · ~/src",)
    assert rows[2].status == "host silent"
    assert rows[3].status == "done 1m ago"
    assert TasksSource(settings).heading(tasks) == "Background tasks / 2 running"


def test_home_relative_only_shortens_paths_inside_home():
    home = str(Path.home())
    assert home_relative(f"{home}/src") == "~/src"
    assert home_relative(f"{home}x/src") == f"{home}x/src"


# Librarian


def test_librarian_skips_entries_without_keys_and_duplicates():
    seen: set[str] = set()
    first = parse_notes([{"key": "a", "title": "A", "type": "decision"}, {"key": None}], seen)
    second = parse_notes([{"key": "a"}, {"key": "b", "content": "# B\n\n- First point"}], seen)
    assert [note.key for note in first + second] == ["a", "b"]
    assert second[0].summary == "First point"


def test_split_frontmatter_returns_top_level_fields_and_the_body():
    fields, body = split_frontmatter(
        '---\ntype: decision\ntags:\n  - x\nsession: "s1"\n---\n# Title\n\nText\n'
    )
    assert fields == [("Type", "decision"), ("Tags", ""), ("Session", "s1")]
    assert body == "# Title\n\nText\n"
    assert split_frontmatter("# No frontmatter") == ([], "# No frontmatter")


def test_librarian_detail_shows_the_note_without_repeating_its_title(settings):
    (settings.iwe_root / "decisions").mkdir()
    (settings.iwe_root / "decisions" / "grid.md").write_text(
        "---\ntype: decision\ncreated: 2026-10-07 06:27\n---\n# Use a grid\n\nPanels sit in a grid.\n",
        encoding="utf-8",
    )
    source = LibrarianSource(settings)
    [row] = source.items([Note("decisions/grid", "Use a grid", "decision", now(), "")])
    detail = source.detail(row)
    assert ("Type", "decision") in detail.fields
    assert detail.sections[0].markdown
    assert detail.sections[0].body == "Panels sit in a grid.\n"
