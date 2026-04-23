"""Hot-path micro-benchmarks for freeorion-tui.

These are intentionally minimal — we just want to know the order-of-
magnitude cost of the things the player hits repeatedly:

1. ``Game.advance_turn()`` — the per-tick cost.
2. ``MapView.render_line(y)`` — one row of the star map.
3. Full-viewport render (all rows).
4. Cursor move (``move_cursor_to_nearest``) — the most common UI action.

Run via ``python -m tests.perf``. Prints timings and a small summary.
"""

from __future__ import annotations

import asyncio
import time

from freeorion_tui.app import FreeOrionApp
from freeorion_tui.engine import new_game


def bench_turn_advance(n: int = 200) -> float:
    g = new_game(seed=42, size=40)
    t0 = time.perf_counter()
    for _ in range(n):
        g.advance_turn()
    dt = time.perf_counter() - t0
    return dt / n * 1000  # ms/turn


async def _run_render_benches(n: int = 100) -> tuple[float, float, float]:
    app = FreeOrionApp(seed=42, galaxy_size=40)
    async with app.run_test(size=(180, 60)) as pilot:
        await pilot.pause()
        mv = app.map_view
        # Single-line render.
        t0 = time.perf_counter()
        for _ in range(n):
            mv.render_line(10)
        per_line = (time.perf_counter() - t0) / n * 1000
        # Full-viewport render (40 rows).
        t0 = time.perf_counter()
        for _ in range(n):
            for y in range(mv.grid_h):
                mv.render_line(y)
        per_full = (time.perf_counter() - t0) / n * 1000
        # Cursor move.
        t0 = time.perf_counter()
        for i in range(n):
            mv.move_cursor_to_nearest(1 if i % 2 == 0 else -1, 0)
        per_cursor = (time.perf_counter() - t0) / n * 1000
    return per_line, per_full, per_cursor


def main() -> None:
    ms_turn = bench_turn_advance()
    per_line, per_full, per_cursor = asyncio.run(_run_render_benches())
    print("freeorion-tui perf")
    print("  advance_turn   : {:6.2f} ms/turn".format(ms_turn))
    print("  render_line    : {:6.3f} ms/line".format(per_line))
    print("  full viewport  : {:6.2f} ms/frame (40 rows)".format(per_full))
    print("  cursor_jump    : {:6.3f} ms/call".format(per_cursor))


if __name__ == "__main__":
    main()
