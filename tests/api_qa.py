"""REST API scenarios for freeorion-tui.

Spins up the agent API on a free port, hits every endpoint, asserts
response shape and round-trips (e.g. enqueue tech then GET /state shows
it in the queue). Keeps the port off 8789 to avoid clashing with a real
dev server.
"""

from __future__ import annotations

import asyncio
import json
import socket
import traceback
import urllib.request

from freeorion_tui.agent_api import start_server
from freeorion_tui.app import FreeOrionApp


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _get(port: int, path: str) -> dict:
    # Run sync urllib in a thread so we don't block the aiohttp server's
    # event loop (they share this test's loop).
    def _do():
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}{path}", timeout=5
        ) as r:
            return json.loads(r.read())
    return await asyncio.to_thread(_do)


async def _post(port: int, path: str, body: dict) -> dict:
    def _do():
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())
    return await asyncio.to_thread(_do)


async def run_all() -> int:
    port = _free_port()
    app = FreeOrionApp(seed=2024, galaxy_size=30)
    runner = await start_server(app, port=port)
    failures: list[str] = []
    try:
        async def check(name: str, coro) -> None:
            try:
                await coro
                print(f"  {name:<34}  \033[32mPASS\033[0m")
            except AssertionError as e:
                failures.append(name)
                print(f"  {name:<34}  \033[31mFAIL\033[0m")
                print(f"    {e}")
            except Exception as e:  # noqa: BLE001
                failures.append(name)
                print(f"  {name:<34}  \033[31mFAIL\033[0m")
                print(f"    {type(e).__name__}: {e}")
                for line in traceback.format_exc().splitlines()[-3:]:
                    print(f"    {line}")

        async def t_state():
            s = await _get(port, "/state")
            assert "turn" in s and "player" in s, s
            assert s["player"]["name"] == app.game.player().name

        async def t_galaxy():
            g = await _get(port, "/galaxy")
            assert len(g["systems"]) == len(app.game.systems)
            assert any(sy.get("planets") for sy in g["systems"])

        async def t_techs():
            t = await _get(port, "/techs")
            assert len(t["techs"]) > 10
            assert "LEARNING_CATEGORY" in t["categories"]

        async def t_research_ok():
            p = app.game.player()
            avail = [tt for tt in p.available_techs()
                     if tt.name not in p.research_queue][:1]
            assert avail, "no tech available to queue"
            r = await _post(port, "/research", {"tech": avail[0].name})
            assert r["ok"], r
            assert avail[0].name in r["queue"]

        async def t_research_bad():
            r = await _post(port, "/research", {"tech": "NOT_A_TECH"})
            assert r["ok"] is False

        async def t_advance():
            t0 = app.game.turn
            r = await _post(port, "/advance", {"turns": 3})
            assert r["ok"] and r["turn"] == t0 + 3

        async def t_produce():
            p = app.game.player()
            planets = app.game.planets_of(p.id)
            assert planets
            initial = len(p.production_queue)
            r = await _post(port, "/produce",
                            {"planet": planets[0].id, "name": "Scout"})
            assert r["ok"], r
            assert len(p.production_queue) == initial + 1

        async def t_focus():
            p = app.game.player()
            planets = app.game.planets_of(p.id)
            target = planets[0]
            r = await _post(port, "/focus",
                            {"planet": target.id, "focus": "research"})
            assert r["ok"]
            assert target.focus == "research"

        async def t_move():
            p = app.game.player()
            fleets = app.game.fleets_of(p.id)
            assert fleets
            home = app.game.system(p.home_system_id)
            if not home.starlanes:
                return
            dest = next(iter(home.starlanes))
            fleets[0].dest_id = None
            fleets[0].eta = 0
            r = await _post(port, "/move",
                            {"fleet": fleets[0].id, "dest": dest})
            assert r["ok"], r

        async def t_events():
            e = await _get(port, "/events")
            assert "events" in e

        await check("/state", t_state())
        await check("/galaxy", t_galaxy())
        await check("/techs", t_techs())
        await check("/research ok", t_research_ok())
        await check("/research bad tech", t_research_bad())
        await check("/advance 3", t_advance())
        await check("/produce scout", t_produce())
        await check("/focus research", t_focus())
        await check("/move fleet", t_move())
        await check("/events", t_events())
    finally:
        await runner.cleanup()
    if failures:
        print(f"\n{len(failures)} failures: {failures}")
        return len(failures)
    print("\nall agent-API scenarios passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run_all()))
