# FreeOrion TUI — design decisions

## Engine integration approach

**Chosen:** Standalone pure-Python simulation that mirrors FreeOrion's data
model, seeded with real FreeOrion content (tech definitions, star name
lists, empire colours) extracted from the vendored repo's data files.

**Rejected:** Compiling FreeOrion's C++ engine and wrapping it via
Boost.Python. The engine is ~340 MB of source with heavy dependencies
(Boost 1.70+, Python 3.8+ boost-python, OGRE/GG UI toolkit, libvorbis,
OpenAL). A full build on taro exceeds the scope of a single session, and
much of what we would compile is UI/network glue we don't need for a TUI.

**Rejected:** Driving the FreeOrion headless server via its client
protocol. `freeoriond` needs the engine compiled first (same blocker).
The protocol is also undocumented as a public API — Boost.Serialization
over TCP, which ties the client to the server's C++ object graph.

**Rationale:** FreeOrion already ships `.focs.py` files that define every
tech, building, species, ship hull, and ship part in a declarative Python
dialect. Parsing those files gives us the full FreeOrion content tree
(194 techs across 8 categories) without running the engine. The
simulation layer (turn advance, research points, fleet movement,
production queue) is implementable in ~300 lines of idiomatic Python.
This is faithful to the FreeOrion player experience — same techs, same
category taxonomy, same galaxy generation feel — while being tractable.

**Future upgrade path:** If someone later compiles the engine, the
binding shim in `freeorion_tui/engine.py` can be swapped to delegate to
the real `freeorion` module; the TUI layer shouldn't need to change.

## Data layout

- `vendor/freeorion/` — full vendored repo (gitignored, fetched via
  `make bootstrap`).
- `freeorion_tui/content.py` — parses `.focs.py` tech files at
  import-time, producing a `TECHS` dict.
- `freeorion_tui/engine.py` — the in-memory galaxy + empire + turn
  engine. Exposes a `Game` class with `advance_turn()`, `research()`,
  `enqueue_production()`, `move_fleet()`.

## Scope gates (per skill Stage 6)

Phase A — Star map + tech tree + production queue + end-turn. Required
for MVP.

Phase B — Graphs + overlays + diplomacy stub (no combat yet).

Phase C — Agent REST API.

Phase D — Optional sound (FreeOrion ships OGG music; WAV playback only
if easy).

Combat and diplomacy detail are explicitly deferred per the user brief.

## Stage 5/6 benchmarks

Baseline numbers after stages 1-4 landed green (python 3.12, 40-system
galaxy seed=42):

```
advance_turn   :   0.05 ms/turn
render_line    :  0.060 ms/line
full viewport  :   2.35 ms/frame (40 rows)
cursor_jump    :  0.048 ms/call
```

All four metrics sit well under the TUI "comfortable interaction" budget
(~16 ms for 60 Hz). The star-map draw path already uses cached Style
objects, serial-invalidated lane/fleet layers, and run-length segment
packing — no further optimisation warranted. No brittle ctypes-pointer
work here (no vendored binary to rebuild; content parsing is pure
Python), so the Stage 6 hardening pass is minimal: focus-key changed
from `o` (now overlay) to `p`, and overlay invalidates the serial cache
so the next paint picks up new tints.

## Stage 7 phases landed

- Phase B (submenus/overlays): `GalaxyScreen`, `TechTreeScreen`,
  `EmpireScreen`, `ResearchQueueScreen`, `HelpScreen` in `screens.py`;
  new keys `G T E R ?`. Map overlay cycle on `o`: none → owners →
  population → research, with a red→yellow→green heat ramp.
- Phase C (agent REST API): full `aiohttp` server — `/state /galaxy
  /techs /research /produce /move /focus /advance /events`. 10/10 API
  scenarios green.
- Phase E (polish): pickle-based save/load to
  `~/.local/share/freeorion-tui/saves/` via `S` and `L` modals.
  Colonise action on `c` for the cursor system (requires a player fleet
  present + habitable unowned planet).

Deferred phases: D (sound — no vendor WAVs in FreeOrion tree that
justify the wire-up), F (animation — rendering is already 2 ms/frame, so
a 2 Hz refresh is a mid-game enhancement, not required for MVP), G (LLM
advisor — scope creep for this session).
