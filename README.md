# freeorion-tui

Terminal-native 4X space strategy, inspired by and seeded with content
from [FreeOrion](https://github.com/freeorion/freeorion). Pure Python
simulation, [Textual](https://github.com/textualize/textual) UI, aiohttp
agent API. No C++ build required.

```
┌── Galaxy · cursor Sol ─────────────────────────────────────────┐
│                  ╱                                              │
│          ★─────╱──✦                                             │
│          │   ╲   │  Terran Federation (YOU)                     │
│          │    ╲  │  Turn: 14                                    │
│  ◄      ★      ╲ ✧     Research Pool: 18.6 RP                   │
│                                                                 │
│                 ►★                                              │
└─────────────────────────────────────────────────────────────────┘
```

## Running

```bash
make         # clone FreeOrion's content tree into engine/ and build venv
make run     # launch the TUI
make test    # 19-scenario QA harness
```

The vendored FreeOrion repo (~340 MB) is only needed for real tech/star
data; the game will fall back to a built-in mini tech tree if it's absent.

### CLI flags

| flag | default | effect |
| --- | --- | --- |
| `--seed N` | random | deterministic galaxy |
| `--size N` | 40 | number of star systems |
| `--agent` | off | start REST API alongside TUI |
| `--agent-port N` | 8789 | agent API port |
| `--headless` | off | no TUI — just sim + API (for autonomous play) |

## Keys

### Map mode
| key | action |
| --- | --- |
| `↑↓←→` | jump to nearest star in that direction |
| `space` | end turn |
| `m` / `t` | focus map / tech browser |
| `f` | build Scout at cursor system |
| `c` | colonise unowned habitable planet at cursor (needs fleet) |
| `p` | cycle focus on owned planets at cursor |
| `g` | send idle fleet to cursor system |
| `o` | cycle map overlay (none → owners → pop → research) |
| `G T E R` | Galaxy / TechTree / Empires / Queue screens |
| `S L` | Save / Load |
| `?` | help |

### Tech mode
| key | action |
| --- | --- |
| `↑↓` | move cursor |
| `←→` | collapse / expand category |
| `enter` | queue tech for research (or expand) |

## Agent API

When `--agent` is on (or `--headless` for no TUI), the game exposes a
REST surface on `127.0.0.1:8789`:

```
GET  /state           compact snapshot (turn, player, pools, queue…)
GET  /galaxy          full dump: systems, planets, lanes, fleets
GET  /techs           all tech definitions + categories
POST /research        {"tech": "<NAME>"} → enqueue
POST /produce         {"planet": id, "name": "Scout"} → enqueue
POST /move            {"fleet": id, "dest": sys_id}
POST /focus           {"planet": id, "focus": "research|industry|…"}
POST /advance         {"turns": N} → batch end-turn
GET  /events          last 30 log entries
```

See `tests/api_qa.py` for a working round-trip smoke test.

## Architecture

- `freeorion_tui/content.py` — parses FreeOrion's vendored `.focs.py`
  tech files at import time. Produces a `TECHS` dict keyed by name
  plus a `CATEGORIES` table. Falls back to a hand-authored mini tech
  tree if the vendor tree is absent, so the game always boots.
- `freeorion_tui/engine.py` — galaxy generator (Poisson sampling +
  nearest-neighbour starlane graph with connectivity repair), data
  model dataclasses, `Game.advance_turn()` loop. Pure Python, pickles
  cleanly for save/load.
- `freeorion_tui/app.py` — Textual `App`, `MapView` (`ScrollView` with
  pre-parsed `Style` cache), side panels, action handlers.
- `freeorion_tui/screens.py` — modal dialogs for Galaxy / Empires /
  Tech tree / Queue editor / Save / Load. Helpers for sparkline + heat
  colour ramp.
- `freeorion_tui/agent_api.py` — aiohttp routes for the agent surface.

## Why not compile the real engine?

See `DECISIONS.md`. Short version: FreeOrion's C++ engine is ~340 MB of
source with heavy Boost/OGRE/OpenAL dependencies that are about UI and
networking — not gameplay. The content we care about (194 techs across
8 categories, star names, empire colours, planet types) all lives in
declarative `.focs.py` files we can parse without running the engine.

## Status

- [x] Stage 1 — research
- [x] Stage 2 — content parsing + pure-Python engine
- [x] Stage 3 — Textual TUI scaffold (map + panels + tech browser)
- [x] Stage 4 — 19-scenario QA harness
- [x] Stage 5 — perf baseline (0.05 ms/turn, 2.35 ms/frame)
- [x] Stage 6 — robustness (overlay invalidates serial, safe defaults)
- [x] Phase B — submenus + overlays
- [x] Phase C — agent REST API
- [x] Phase E (partial) — save / load / colonise
- [ ] Phase D — sound (deferred — no vendor WAVs)
- [ ] Phase F — animation (deferred — rendering is already cheap)
- [ ] Phase G — LLM advisor (deferred)

## License

FreeOrion is GPLv3; vendored at `engine/freeorion/` (not checked in —
run `make bootstrap`). This wrapper inherits GPLv3 from the content we
parse.
