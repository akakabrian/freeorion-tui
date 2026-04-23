"""REST API for an external agent (LLM or scripted) to play the game.

Hosted on the same asyncio loop as the Textual app via ``aiohttp``.
Minimal surface mirroring what a player does from the TUI:

- ``GET  /state``         — compact snapshot (same as LLM advisor sees)
- ``GET  /galaxy``        — full galaxy dump (systems, lanes, planets)
- ``GET  /techs``         — list of tech definitions
- ``POST /research {tech}``         — enqueue tech
- ``POST /produce {planet, name}``  — enqueue ship production
- ``POST /move {fleet, dest}``      — send fleet to system
- ``POST /focus {planet, focus}``   — set planet focus
- ``POST /advance {turns}``         — end ``turns`` turns
- ``GET  /events``        — recent log tail (polling; no SSE)
"""

from __future__ import annotations

from aiohttp import web

from . import content


def state_snapshot(app) -> dict:
    """Shared snapshot — mirrored by LLM advisor + agent API."""
    return app.game.state_snapshot()


def _galaxy_dump(game) -> dict:
    systems = []
    for s in game.systems:
        systems.append({
            "id": s.id,
            "name": s.name,
            "x": s.x,
            "y": s.y,
            "star_type": s.star_type,
            "lanes": sorted(s.starlanes),
            "planets": [
                {
                    "id": p.id, "name": p.name, "type": p.type, "size": p.size,
                    "owner": p.owner, "population": p.population,
                    "max_population": p.max_population, "focus": p.focus,
                }
                for p in s.planets
            ],
            "owner": s.owner,
        })
    empires = [
        {
            "id": e.id, "name": e.name, "color": e.color,
            "is_player": e.is_player, "home_system_id": e.home_system_id,
            "researched": sorted(e.researched),
            "queue": list(e.research_queue),
            "rp_pool": e.rp_pool, "pp_pool": e.pp_pool,
        }
        for e in game.empires
    ]
    fleets = [
        {"id": f.id, "owner": f.owner, "name": f.name,
         "system_id": f.system_id, "dest_id": f.dest_id,
         "eta": f.eta, "ships": f.ships}
        for f in game.fleets
    ]
    return {
        "turn": game.turn, "systems": systems,
        "empires": empires, "fleets": fleets,
    }


async def handle_state(request: web.Request) -> web.Response:
    app = request.app["fo_app"]
    return web.json_response(state_snapshot(app))


async def handle_galaxy(request: web.Request) -> web.Response:
    app = request.app["fo_app"]
    return web.json_response(_galaxy_dump(app.game))


async def handle_techs(request: web.Request) -> web.Response:
    techs = [
        {"name": t.name, "short_name": t.short_name, "category": t.category,
         "cost": t.cost, "turns": t.turns,
         "prerequisites": t.prerequisites}
        for t in content.TECHS.values()
    ]
    return web.json_response({"techs": techs, "categories": {
        k: {"name": c.name, "short_name": c.short_name, "color": c.color}
        for k, c in content.CATEGORIES.items()
    }})


async def handle_research(request: web.Request) -> web.Response:
    app = request.app["fo_app"]
    data = await request.json()
    tech = data.get("tech")
    p = app.game.player()
    ok = app.game.enqueue_research(p.id, tech)
    return web.json_response({"ok": ok, "queue": list(p.research_queue)})


async def handle_produce(request: web.Request) -> web.Response:
    app = request.app["fo_app"]
    data = await request.json()
    planet = int(data.get("planet", -1))
    name = data.get("name", "Scout")
    kind = data.get("kind", "ship")
    p = app.game.player()
    ok = app.game.enqueue_production(p.id, planet, kind=kind, name=name)
    return web.json_response({"ok": ok})


async def handle_move(request: web.Request) -> web.Response:
    app = request.app["fo_app"]
    data = await request.json()
    fleet = int(data.get("fleet", -1))
    dest = int(data.get("dest", -1))
    ok = app.game.move_fleet(fleet, dest)
    return web.json_response({"ok": ok})


async def handle_focus(request: web.Request) -> web.Response:
    app = request.app["fo_app"]
    data = await request.json()
    planet_id = int(data.get("planet", -1))
    focus = data.get("focus", "industry")
    if focus not in ("research", "industry", "population", "none"):
        return web.json_response({"ok": False, "error": "bad focus"}, status=400)
    for s in app.game.systems:
        for p in s.planets:
            if p.id == planet_id:
                p.focus = focus
                app.game.bump()
                return web.json_response({"ok": True})
    return web.json_response({"ok": False, "error": "planet not found"}, status=404)


async def handle_advance(request: web.Request) -> web.Response:
    app = request.app["fo_app"]
    data = await request.json()
    turns = int(data.get("turns", 1))
    turns = max(1, min(50, turns))  # rate-limit runaway calls
    events_all: list[str] = []
    for _ in range(turns):
        events_all.extend(app.game.advance_turn())
    return web.json_response({
        "ok": True, "turn": app.game.turn,
        "events": events_all,
    })


async def handle_events(request: web.Request) -> web.Response:
    app = request.app["fo_app"]
    return web.json_response({"events": app.game.log[-30:]})


def make_app(fo_app) -> web.Application:
    app = web.Application()
    app["fo_app"] = fo_app
    app.add_routes([
        web.get("/state", handle_state),
        web.get("/galaxy", handle_galaxy),
        web.get("/techs", handle_techs),
        web.get("/events", handle_events),
        web.post("/research", handle_research),
        web.post("/produce", handle_produce),
        web.post("/move", handle_move),
        web.post("/focus", handle_focus),
        web.post("/advance", handle_advance),
    ])
    return app


async def start_server(fo_app, port: int = 8789, host: str = "127.0.0.1"):
    runner = web.AppRunner(make_app(fo_app))
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    return runner
