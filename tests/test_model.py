from datetime import UTC, datetime, timedelta

from fieldnotes.config import Settings, parse_args
from fieldnotes.model import Item, ago, parse_time, span, until


def test_span_is_compact():
    assert span(30) == "<1m"
    assert span(12 * 60) == "12m"
    assert span(3 * 3600 + 5 * 60) == "3h 05m"
    assert span(2 * 86400 + 4 * 3600) == "2d 4h"


def test_ago_and_until_count_from_the_given_moment():
    at = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
    assert ago(at - timedelta(minutes=90), at) == "1h 30m"
    assert ago(None, at) == "—"
    assert until(at + timedelta(minutes=5), at) == "5m"
    assert until(at - timedelta(minutes=5), at) == "now"


def test_parse_time_accepts_zulu_offsets_and_local_times():
    utc = parse_time("2026-10-07T12:00:00Z")
    assert utc == datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
    assert parse_time("2026-10-07T07:00:00-05:00") == utc
    # IWE writes local times without a zone.
    local = parse_time("2026-10-07 06:27")
    assert local is not None and local.tzinfo is not None
    assert parse_time("not a time") is None
    assert parse_time(None) is None


def test_item_matches_every_word_anywhere_in_the_row():
    item = Item(
        "k", "nix-ci on main", status="failing ×2", lines=("Build checks",), value="nix-ci.yml"
    )
    assert item.matches("")
    assert item.matches("NIX failing")
    assert item.matches("yml checks")
    assert not item.matches("nix release")


def test_settings_read_the_environment_and_flags_win():
    environ = {
        "FIELDNOTES_CI_REPO": "someone/else",
        "FIELDNOTES_POLL_SECONDS": "1",
        "FIELDNOTES_IWE_ROOT": "~/notes",
        "XDG_STATE_HOME": "/state",
    }
    settings = Settings.from_env(environ)
    assert settings.ci_repo == "someone/else"
    # Polling faster than every five seconds would hammer the remote APIs.
    assert settings.poll_seconds == 5
    assert str(settings.iwe_root).endswith("/notes")
    assert str(settings.tasks_dir) == "/state/fieldnotes/agent-tasks"
    flags = parse_args(["--ci-repo", "flag/repo", "--poll-seconds", "60"], environ)
    assert flags.ci_repo == "flag/repo"
    assert flags.poll_seconds == 60
    assert flags.iwe_root == settings.iwe_root
