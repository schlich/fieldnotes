"""Drive the app headless through every mode."""

from conftest import run, settle

from fieldnotes.app import FieldnotesApp
from fieldnotes.model import SourceError
from fieldnotes.screens import ConfirmScreen, DashboardScreen, DetailScreen
from fieldnotes.widgets import HintBar, StatusLine, binding_chips


def hints(screen) -> dict[str, str]:
    return {description: keys for keys, description, enabled in binding_chips(screen) if enabled}


def mode(screen) -> str:
    return screen.query_one(StatusLine).mode


def test_normal_mode_shows_the_keys_for_the_focused_row(app):
    async def scenario():
        async with app.run_test(size=(140, 45)) as pilot:
            dashboard = await settle(pilot, app)
            assert mode(dashboard) == "NORMAL"
            assert dashboard.panel("usage").list.has_focus
            usage = hints(dashboard)
            assert usage["Move"] == "j/k"
            assert usage["Panel"] == "tab/⇧tab/1-5"
            # Usage rows have nothing to open and nothing to rerun.
            assert "Open" not in usage and "Rerun failed" not in usage
            assert "Copy usage" in usage

            await pilot.press("2")
            ci = dashboard.panel("ci").list
            assert ci.has_focus
            assert ci.current.key == "main/nix-ci"
            row = hints(dashboard)
            assert {"Open", "Copy URL", "Copy workflow file", "Rerun failed"} <= row.keys()
            assert "2 CI" in dashboard.query_one(StatusLine).left.plain

            await pilot.press("j")
            assert ci.current.key == "main/release"
            assert "Rerun failed" not in hints(dashboard)

    run(scenario)


def test_status_line_summarizes_every_source(app):
    async def scenario():
        async with app.run_test(size=(140, 45)) as pilot:
            dashboard = await settle(pilot, app)
            left = dashboard.query_one(StatusLine).left.plain
            assert "usage 1 over pace" in left
            assert "CI 2 failing" in left
            assert "2 waiting, 1 over 2d" in left

    run(scenario)


def test_filter_mode_narrows_rows_and_esc_clears_it(app):
    async def scenario():
        async with app.run_test(size=(140, 45)) as pilot:
            dashboard = await settle(pilot, app)
            await pilot.press("2", "slash")
            assert mode(dashboard) == "FILTER"
            assert set(hints(dashboard)) == {"Keep filter", "Clear filter", "Panel"}
            await pilot.press(*"release")
            panel = dashboard.panel("ci")
            assert [item.key for item in panel.list.items] == ["main/release"]
            assert panel.border_subtitle == "/release  1 of 5"

            await pilot.press("enter")
            assert mode(dashboard) == "NORMAL"
            assert panel.list.has_focus
            assert panel.filter == "release"
            assert "Clear filter" in hints(dashboard)

            await pilot.press("escape")
            assert panel.filter == ""
            assert len(panel.list.items) == 5
            assert "Clear filter" not in hints(dashboard)

    run(scenario)


def test_filter_survives_leaving_the_field_with_tab(app):
    async def scenario():
        async with app.run_test(size=(140, 45)) as pilot:
            dashboard = await settle(pilot, app)
            await pilot.press("3", "slash", *"fresh", "tab")
            topics = dashboard.panel("topics")
            assert topics.filter == "fresh"
            assert not topics.filtering
            assert dashboard.panel("tasks").list.has_focus
            assert mode(dashboard) == "NORMAL"

    run(scenario)


def test_detail_mode_opens_with_enter_and_returns_with_esc(app, fakes):
    async def scenario():
        async with app.run_test(size=(140, 45)) as pilot:
            dashboard = await settle(pilot, app)
            await pilot.press("3", "enter")
            await pilot.pause(0.1)
            detail = app.screen
            assert isinstance(detail, DetailScreen)
            assert mode(detail) == "DETAIL"
            assert {"Back", "Scroll", "Page", "Open", "Copy URL", "Copy branch"} <= hints(
                detail
            ).keys()
            # The dashboard's own bars give way while another screen is on top.
            assert not dashboard.query_one(StatusLine).display
            await pilot.press("escape")
            assert app.screen is dashboard
            assert dashboard.query_one(StatusLine).display
            assert dashboard.query_one(HintBar).display
            assert mode(dashboard) == "NORMAL"

    run(scenario)


def test_rerun_asks_first_and_runs_only_on_yes(app, fakes):
    async def scenario():
        async with app.run_test(size=(140, 45)) as pilot:
            dashboard = await settle(pilot, app)
            await pilot.press("2", "x")
            confirm = app.screen
            assert isinstance(confirm, ConfirmScreen)
            assert mode(confirm) == "CONFIRM"
            assert set(hints(confirm)) == {"Confirm", "Cancel"}
            assert not dashboard.query_one(StatusLine).display

            await pilot.press("n")
            assert app.screen is dashboard
            assert fakes["ci"].reruns == []

            await pilot.press("x", "y")
            await pilot.pause(0.1)
            assert fakes["ci"].reruns == ["main/nix-ci"]
            assert app.screen is dashboard

    run(scenario)


def test_copy_goes_through_the_terminal_clipboard(app):
    async def scenario():
        async with app.run_test(size=(140, 45)) as pilot:
            await settle(pilot, app)
            await pilot.press("3", "Y")
            assert app.clipboard == "jj-stale-topic-kkkkkkkk"
            await pilot.press("y")
            assert app.clipboard.endswith("/compare/main...jj-stale-topic-kkkkkkkk")

    run(scenario)


def test_open_copies_the_url_where_there_is_no_desktop(app, monkeypatch):
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)

    async def scenario():
        async with app.run_test(size=(140, 45)) as pilot:
            await settle(pilot, app)
            await pilot.press("2", "o")
            assert app.clipboard == "https://github.com/example/repo/actions/runs/10"

    run(scenario)


def test_panel_keys_cycle_and_jump(app):
    async def scenario():
        async with app.run_test(size=(140, 45)) as pilot:
            dashboard = await settle(pilot, app)
            order = ["usage", "ci", "topics", "tasks", "librarian"]
            for name in order[1:] + order[:1]:
                await pilot.press("tab")
                assert dashboard.focused_panel.source.name == name
            await pilot.press("shift+tab")
            assert dashboard.focused_panel.source.name == "librarian"
            await pilot.press("4")
            assert dashboard.focused_panel.source.name == "tasks"
            assert dashboard.panel("tasks").border_title == "4 Background tasks / 0 running"

    run(scenario)


def test_layout_follows_the_terminal_width(app):
    async def scenario():
        for size, expected in (
            ((80, 40), "-narrow"),
            ((140, 45), "-wide"),
            ((200, 50), "-very-wide"),
        ):
            async with FieldnotesApp(app.settings, app.sources).run_test(size=size) as pilot:
                await pilot.pause()
                assert expected in pilot.app.screen.classes, size

    run(scenario)


def test_a_failing_source_shows_its_error_and_keeps_the_rest(settings, fakes):
    fakes["topics"].error = SourceError("Tangled knot unreachable: timed out")
    app = FieldnotesApp(settings, [fake.source for fake in fakes.values()])

    async def scenario():
        async with app.run_test(size=(140, 45)) as pilot:
            dashboard = await settle(pilot, app)
            assert isinstance(dashboard, DashboardScreen)
            message = dashboard.panel("topics").message
            assert message.plain == "Unavailable: Tangled knot unreachable: timed out"
            assert dashboard.panel("ci").list.items
            assert "1 unavailable" in dashboard.query_one(StatusLine).right.plain

    run(scenario)


def test_an_unexpected_exception_in_one_source_does_not_stop_the_app(settings, fakes):
    fakes["usage"].error = RuntimeError("bad JSON shape")
    app = FieldnotesApp(settings, [fake.source for fake in fakes.values()])

    async def scenario():
        async with app.run_test(size=(140, 45)) as pilot:
            dashboard = await settle(pilot, app)
            assert dashboard.states["usage"].error == "RuntimeError: bad JSON shape"
            assert app.is_running

    run(scenario)


def test_hint_bar_wraps_whole_chips_onto_more_lines(app):
    async def scenario():
        async with app.run_test(size=(60, 40)) as pilot:
            dashboard = await settle(pilot, app)
            await pilot.press("2")
            bar = dashboard.query_one(HintBar)
            lines = bar.lines(56)
            assert len(lines) > 1
            for line in lines:
                width = sum(len(keys) + 1 + len(text) for keys, text, _ in line) + 3 * (
                    len(line) - 1
                )
                assert width <= 56 or len(line) == 1
            assert bar.size.height == len(bar.lines(bar.content_size.width))

    run(scenario)
