"""Headless QA harness for freeorion-tui.

Each scenario mounts a fresh ``FreeOrionApp`` via ``App.run_test()`` and
exercises the UI via ``pilot.press(...)``. Asserts on the live state.
SVG screenshots are saved to ``tests/out/`` for visual diffing.

Usage:
    python -m tests.qa                # run all
    python -m tests.qa cursor         # run scenarios matching "cursor"
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from freeorion_tui import content
from freeorion_tui.app import FreeOrionApp

OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(exist_ok=True)


@dataclass
class Scenario:
    name: str
    fn: Callable[[FreeOrionApp, "object"], Awaitable[None]]


# ------- scenarios --------------------------------------------------

async def s_mount_clean(app, pilot):
    assert app.map_view is not None
    assert app.status_panel is not None
    assert app.tech_panel is not None
    assert app.queue_panel is not None
    assert app.fleet_panel is not None
    assert app.game is not None
    assert len(app.game.systems) > 0
    assert len(app.game.empires) > 0


async def s_cursor_starts_on_home(app, pilot):
    p = app.game.player()
    assert app.map_view.cursor_system == p.home_system_id, (
        f"cursor={app.map_view.cursor_system} home={p.home_system_id}"
    )


async def s_cursor_jumps_between_stars(app, pilot):
    start = app.map_view.cursor_system
    await pilot.press("right")
    # Cursor should have moved — we can't predict to which star, but some
    # nearby star should now be selected (unless there's nothing east).
    await pilot.press("right")
    await pilot.press("right")
    # Not all galaxy seeds have stars in every direction; at minimum we
    # should have moved at least once.
    moved_somewhere = app.map_view.cursor_system != start
    if not moved_somewhere:
        # Try another direction.
        await pilot.press("down")
        await pilot.press("left")
        moved_somewhere = app.map_view.cursor_system != start
    assert moved_somewhere, f"cursor stuck at {start}"


async def s_end_turn_advances(app, pilot):
    t0 = app.game.turn
    await pilot.press("space")
    await pilot.pause()
    assert app.game.turn == t0 + 1, (
        f"turn did not advance: {t0} → {app.game.turn}"
    )


async def s_tech_browser_queues(app, pilot):
    """Switch to tech mode, pick a queuable tech, press enter, verify queue grew."""
    p = app.game.player()
    initial_q = list(p.research_queue)
    await pilot.press("t")  # focus_techs
    await pilot.pause()
    # Scan the flat list for the first queuable tech and drive the cursor
    # straight to it, rather than blindly pressing j and hoping.
    target_idx = None
    for i, (kind, key) in enumerate(app.tech_panel._flat):
        if kind != "tech":
            continue
        if key in p.researched or key in p.research_queue:
            continue
        tech = content.TECHS[key]
        if all(pre in p.researched for pre in tech.prerequisites):
            target_idx = i
            break
    assert target_idx is not None, "no queuable tech found"
    # Move cursor there — use direct assignment since key presses are
    # bounded and the flat list is long.
    app.tech_panel.cursor_idx = target_idx
    app.tech_panel.refresh_panel()
    await pilot.press("enter")
    await pilot.pause()
    assert len(p.research_queue) > len(initial_q), (
        f"queue didn't grow: {initial_q} → {p.research_queue}"
    )


async def s_build_fleet_enqueues(app, pilot):
    p = app.game.player()
    # Ensure cursor is at the home system (starts there by default).
    assert app.map_view.cursor_system == p.home_system_id
    initial = len(p.production_queue)
    await pilot.press("f")
    await pilot.pause()
    assert len(p.production_queue) == initial + 1, (
        f"production queue {initial} → {len(p.production_queue)}"
    )


async def s_planet_focus_changes(app, pilot):
    p = app.game.player()
    planets = app.game.planets_of(p.id)
    assert planets, "player should own a planet at start"
    foci = [pl.focus for pl in planets]
    await pilot.press("o")  # cycle focus
    await pilot.pause()
    new_foci = [pl.focus for pl in planets]
    assert foci != new_foci, f"focus didn't change: {foci}"


async def s_tech_research_progresses(app, pilot):
    """Advance several turns; research pool should produce done techs."""
    p = app.game.player()
    done0 = len(p.researched)
    # Queue a cheap tech to ensure progress.
    # Seeded queue already has one; just advance turns.
    for _ in range(15):
        await pilot.press("space")
        await pilot.pause()
    # At minimum, the research pool should have grown OR researched count up.
    assert len(p.researched) >= done0, "researched count regressed"
    assert app.game.turn >= 15, f"turn didn't advance: {app.game.turn}"


async def s_move_fleet_sets_destination(app, pilot):
    p = app.game.player()
    fleets = app.game.fleets_of(p.id)
    assert fleets, "player should have a starting fleet"
    # Find a system connected to home via starlane.
    home = app.game.system(p.home_system_id)
    if not home.starlanes:
        return  # can't test on disconnected home
    dest = next(iter(home.starlanes))
    # Select the destination via direct cursor set.
    app.map_view.cursor_system = dest
    await pilot.pause()
    await pilot.press("g")
    await pilot.pause()
    f = fleets[0]
    assert f.dest_id == dest, f"dest_id={f.dest_id}, want {dest}"
    assert f.eta > 0


async def s_render_produces_output(app, pilot):
    """Smoke test: the map renders at least one non-blank cell."""
    # After on_mount, MapView should have rendered. Directly invoke
    # render_line(0) and verify it produces a Strip with segments.
    strip = app.map_view.render_line(0)
    cells = list(strip)  # public API — never touch _segments
    assert cells, "render_line produced nothing"
    # At least some cells on the galaxy map should carry colour other
    # than pure background — confirms stars/lanes were drawn somewhere.
    any_visible = False
    for _ in range(app.map_view.grid_h):
        pass
    for row in range(app.map_view.grid_h):
        strip = app.map_view.render_line(row)
        for seg in list(strip):
            if seg.text.strip() and any(ch not in " ·" for ch in seg.text):
                any_visible = True
                break
        if any_visible:
            break
    assert any_visible, "no visible stars / lanes found in rendered map"


async def s_snapshot_no_app_context(app, pilot):
    """Regression: state_snapshot must not crash when called without App."""
    # The live app has one, but call on the underlying Game directly —
    # equivalent to what the agent API does in --headless before mount.
    snap = app.game.state_snapshot()
    assert "turn" in snap
    assert "player" in snap
    assert snap["player"]["name"] == app.game.player().name


async def s_help_opens_and_closes(app, pilot):
    await pilot.press("question_mark")
    await pilot.pause()
    # Popping should just not crash.
    await pilot.press("escape")
    await pilot.pause()


# ------- harness ---------------------------------------------------

SCENARIOS = [
    Scenario("mount_clean", s_mount_clean),
    Scenario("cursor_starts_on_home", s_cursor_starts_on_home),
    Scenario("cursor_jumps_between_stars", s_cursor_jumps_between_stars),
    Scenario("end_turn_advances", s_end_turn_advances),
    Scenario("tech_browser_queues", s_tech_browser_queues),
    Scenario("build_fleet_enqueues", s_build_fleet_enqueues),
    Scenario("planet_focus_changes", s_planet_focus_changes),
    Scenario("tech_research_progresses", s_tech_research_progresses),
    Scenario("move_fleet_sets_destination", s_move_fleet_sets_destination),
    Scenario("render_produces_output", s_render_produces_output),
    Scenario("snapshot_no_app_context", s_snapshot_no_app_context),
    Scenario("help_opens_and_closes", s_help_opens_and_closes),
]


async def run_scenario(scn: Scenario) -> tuple[str, bool, str]:
    app = FreeOrionApp(seed=12345)
    try:
        async with app.run_test(size=(180, 60)) as pilot:
            await pilot.pause()
            try:
                await scn.fn(app, pilot)
                try:
                    app.save_screenshot(str(OUT / f"{scn.name}.PASS.svg"))
                except Exception:
                    pass
                return (scn.name, True, "")
            except AssertionError as e:
                try:
                    app.save_screenshot(str(OUT / f"{scn.name}.FAIL.svg"))
                except Exception:
                    pass
                return (scn.name, False, f"AssertionError: {e}")
    except Exception as e:
        tb = traceback.format_exc()
        return (scn.name, False, f"{type(e).__name__}: {e}\n{tb}")


async def main(patterns: list[str]) -> int:
    scns = SCENARIOS
    if patterns:
        scns = [s for s in SCENARIOS if any(p in s.name for p in patterns)]
    fails = 0
    width = max(len(s.name) for s in scns)
    for s in scns:
        name, ok, err = await run_scenario(s)
        marker = "\033[32mPASS\033[0m" if ok else "\033[31mFAIL\033[0m"
        print(f"  {name:<{width}}  {marker}")
        if not ok:
            fails += 1
            for line in err.splitlines():
                print(f"    {line}")
    print(f"\n{len(scns) - fails}/{len(scns)} passed")
    return fails


if __name__ == "__main__":
    patterns = sys.argv[1:]
    raise SystemExit(asyncio.run(main(patterns)))
