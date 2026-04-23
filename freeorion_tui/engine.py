"""Galaxy + empire + turn engine.

Pure-Python simulation modelled on FreeOrion's core loop. No C++, no
boost.python — we parse real FreeOrion tech definitions and build a
playable universe around them.

Key types
---------

- ``Game``: top-level state. ``advance_turn()`` is the main tick.
- ``System``: a star system with position, name, and optional planet.
- ``Planet``: orbits a system. Produces research and industry when owned.
- ``Fleet``: movable by the player; path via ``move_fleet()``.
- ``Empire``: player/ai. Owns planets, runs a research + production queue.

All state is plain dataclasses so it pickles cleanly (future save/load)
and serialises trivially for the agent API.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Optional

from .content import STAR_NAMES, TECHS, Tech, techs_by_category

# --- constants ------------------------------------------------------

GALAXY_WIDTH = 100.0
GALAXY_HEIGHT = 60.0
MAP_W = 120  # TUI render grid width (chars)
MAP_H = 40   # TUI render grid height
MIN_SYSTEM_SEPARATION = 3.5  # in galaxy units
MAX_STARLANE_LENGTH = 20.0

STAR_TYPES = ["blue", "white", "yellow", "orange", "red", "neutron", "black_hole"]
STAR_WEIGHTS = [6, 10, 15, 10, 15, 2, 1]
PLANET_SIZES = ["tiny", "small", "medium", "large", "huge"]
PLANET_TYPES = ["swamp", "toxic", "inferno", "radiated", "barren", "tundra",
                "desert", "terran", "ocean", "gas_giant", "asteroids"]


# --- data model -----------------------------------------------------

@dataclass
class Planet:
    """A single colonisable body orbiting a star system."""
    id: int
    system_id: int
    name: str
    type: str  # entry from PLANET_TYPES
    size: str  # entry from PLANET_SIZES
    owner: Optional[int] = None  # empire id, None = unoccupied
    population: float = 0.0
    max_population: float = 0.0
    focus: str = "none"  # "research" | "industry" | "population" | "none"

    def is_habitable(self) -> bool:
        return self.type not in ("gas_giant", "asteroids")

    @property
    def research_output(self) -> float:
        if self.focus != "research" or self.population <= 0:
            return 0.0
        return self.population * 0.6

    @property
    def industry_output(self) -> float:
        if self.focus != "industry" or self.population <= 0:
            return 0.0
        return self.population * 0.8

    @property
    def symbol(self) -> str:
        return {
            "gas_giant": "◉",
            "asteroids": "·",
            "terran": "◎",
            "ocean": "◉",
            "desert": "○",
            "tundra": "◌",
            "swamp": "◍",
            "toxic": "◐",
            "inferno": "◑",
            "radiated": "◒",
            "barren": "◓",
        }.get(self.type, "○")


@dataclass
class System:
    """Star system on the galaxy map. One per point in space."""
    id: int
    name: str
    x: float  # galaxy-space coords
    y: float
    star_type: str
    planets: list[Planet] = field(default_factory=list)
    starlanes: set[int] = field(default_factory=set)  # neighbour system ids

    @property
    def owner(self) -> Optional[int]:
        """Most-represented empire across the system's planets, if any."""
        owners = [p.owner for p in self.planets if p.owner is not None]
        if not owners:
            return None
        return max(set(owners), key=owners.count)

    @property
    def star_glyph(self) -> str:
        return {
            "blue": "✦",
            "white": "✧",
            "yellow": "★",
            "orange": "☆",
            "red": "✯",
            "neutron": "✴",
            "black_hole": "●",
        }.get(self.star_type, "*")


@dataclass
class Fleet:
    """Movable group of ships in interstellar space.

    Simplified: fleets are always at a system (``system_id``) or in
    transit along a starlane. When ``dest_id`` is set they are
    travelling; ``eta`` counts down each turn."""
    id: int
    owner: int
    name: str
    system_id: int
    dest_id: Optional[int] = None
    eta: int = 0
    ships: int = 1


@dataclass
class ResearchProgress:
    tech_name: str
    points: float = 0.0

    @property
    def required(self) -> int:
        return TECHS[self.tech_name].cost


@dataclass
class ProductionItem:
    kind: str  # "ship" | "colony_ship" | "building"
    name: str
    location_id: int  # planet id
    progress: float = 0.0
    cost: float = 50.0

    @property
    def percent(self) -> int:
        return int(100 * self.progress / max(self.cost, 1))


@dataclass
class Empire:
    id: int
    name: str
    color: tuple[int, int, int]
    home_system_id: int
    research_queue: list[str] = field(default_factory=list)  # tech names
    researched: set[str] = field(default_factory=set)
    in_progress: Optional[ResearchProgress] = None
    rp_pool: float = 0.0
    pp_pool: float = 0.0  # production points
    production_queue: list[ProductionItem] = field(default_factory=list)
    is_player: bool = False

    def available_techs(self) -> list[Tech]:
        """Techs whose prerequisites are satisfied and that aren't done."""
        out = []
        for t in TECHS.values():
            if t.name in self.researched:
                continue
            if all(pre in self.researched for pre in t.prerequisites):
                out.append(t)
        return out


# --- galaxy generation ---------------------------------------------

def _poisson_points(
    n: int, width: float, height: float, min_dist: float, rng: random.Random,
) -> list[tuple[float, float]]:
    """Brute-force rejection sampler. Good enough for ~30-60 systems."""
    pts: list[tuple[float, float]] = []
    attempts = 0
    margin = 2.0
    while len(pts) < n and attempts < n * 200:
        x = rng.uniform(margin, width - margin)
        y = rng.uniform(margin, height - margin)
        ok = True
        for (px, py) in pts:
            if (x - px) ** 2 + (y - py) ** 2 < min_dist ** 2:
                ok = False
                break
        if ok:
            pts.append((x, y))
        attempts += 1
    return pts


def _build_starlanes(systems: list[System]) -> None:
    """Connect each system to its 2-3 nearest neighbours within MAX length.

    Approximates FreeOrion's own Delaunay-ish lane graph. We aim for a
    connected graph so every star is reachable."""
    n = len(systems)
    for i, s in enumerate(systems):
        dists = []
        for j, t in enumerate(systems):
            if i == j:
                continue
            d = math.hypot(s.x - t.x, s.y - t.y)
            if d <= MAX_STARLANE_LENGTH:
                dists.append((d, j))
        dists.sort()
        for _, j in dists[:3]:  # up to 3 lanes per system
            s.starlanes.add(j)
            systems[j].starlanes.add(i)
    # Ensure connectivity — if we have any isolated group, wire the
    # closest pair between components.
    if n > 1:
        visited = {0}
        stack = [0]
        while stack:
            cur = stack.pop()
            for nb in systems[cur].starlanes:
                if nb not in visited:
                    visited.add(nb)
                    stack.append(nb)
        unvisited = set(range(n)) - visited
        while unvisited:
            best = None
            best_d = float("inf")
            for i in visited:
                for j in unvisited:
                    d = math.hypot(systems[i].x - systems[j].x,
                                   systems[i].y - systems[j].y)
                    if d < best_d:
                        best_d = d
                        best = (i, j)
            if best is None:
                break
            i, j = best
            systems[i].starlanes.add(j)
            systems[j].starlanes.add(i)
            # Flood from j to pick up its cluster.
            stk = [j]
            while stk:
                cur = stk.pop()
                if cur in visited:
                    continue
                visited.add(cur)
                unvisited.discard(cur)
                for nb in systems[cur].starlanes:
                    if nb not in visited:
                        stk.append(nb)


def generate_galaxy(
    size: int = 40,
    seed: Optional[int] = None,
    num_empires: int = 3,
) -> "Game":
    """Create a new Game with a populated galaxy. ``size`` is system count."""
    rng = random.Random(seed)
    points = _poisson_points(size, GALAXY_WIDTH, GALAXY_HEIGHT,
                             MIN_SYSTEM_SEPARATION, rng)
    star_names = rng.sample(STAR_NAMES, min(len(STAR_NAMES), len(points)))
    systems: list[System] = []
    for i, (x, y) in enumerate(points):
        star_type = rng.choices(STAR_TYPES, weights=STAR_WEIGHTS, k=1)[0]
        name = star_names[i] if i < len(star_names) else f"System-{i}"
        s = System(id=i, name=name, x=x, y=y, star_type=star_type)
        # 0-4 planets per system, heavier for friendlier stars.
        n_planets = rng.choices([0, 1, 2, 3, 4], weights=[1, 2, 4, 3, 1], k=1)[0]
        for p_idx in range(n_planets):
            pid = i * 10 + p_idx
            p_type = rng.choice(PLANET_TYPES)
            p_size = rng.choice(PLANET_SIZES)
            p_name = f"{name} {'IVX'[:p_idx+1] or 'I'}"  # rough roman-ish
            base = {"tiny": 2, "small": 4, "medium": 6, "large": 8, "huge": 10}[p_size]
            hab_bonus = 1.5 if p_type in ("terran", "ocean") else (
                1.0 if p_type in ("swamp", "tundra", "desert") else 0.5
            )
            s.planets.append(Planet(
                id=pid, system_id=i, name=p_name, type=p_type, size=p_size,
                max_population=base * hab_bonus,
            ))
        systems.append(s)
    _build_starlanes(systems)
    game = Game(systems=systems, turn=1)
    # Seed empires on the habitable systems — try to pick far-apart stars.
    habitable_ids = [
        s.id for s in systems
        if any(p.is_habitable() and p.max_population >= 4 for p in s.planets)
    ]
    rng.shuffle(habitable_ids)
    empire_colors = [
        (80, 180, 255),  # human blue
        (240, 90, 90),   # red
        (90, 220, 130),  # green
        (240, 220, 90),  # yellow
        (200, 120, 230), # purple
        (240, 150, 80),  # orange
    ]
    empire_names = ["Terran Federation", "Sythran Imperium", "Morrh Collective",
                    "Ikri League", "Volsh Dominion", "Cauxinari Council"]
    picked: list[int] = []
    for e_idx in range(num_empires):
        chosen = None
        for sid in habitable_ids:
            s = systems[sid]
            if any(math.hypot(s.x - systems[p].x, s.y - systems[p].y) < 15.0
                   for p in picked):
                continue
            chosen = sid
            picked.append(sid)
            break
        if chosen is None and habitable_ids:
            chosen = habitable_ids[e_idx % len(habitable_ids)]
            picked.append(chosen)
        if chosen is None:
            continue
        home = systems[chosen]
        best_planet = max(
            (p for p in home.planets if p.is_habitable()),
            key=lambda p: p.max_population,
            default=None,
        )
        emp = Empire(
            id=e_idx,
            name=empire_names[e_idx % len(empire_names)],
            color=empire_colors[e_idx % len(empire_colors)],
            home_system_id=chosen,
            is_player=(e_idx == 0),
        )
        if best_planet is not None:
            best_planet.owner = e_idx
            best_planet.population = best_planet.max_population * 0.6
            best_planet.focus = "research" if e_idx == 0 else "industry"
        # Give empire a starting fleet at home.
        game.fleets.append(Fleet(
            id=len(game.fleets),
            owner=e_idx,
            name=f"{emp.name} Home Fleet",
            system_id=chosen,
            ships=3,
        ))
        # Seed a small research pipeline — first few techs from each
        # category in priority order so something always ticks down.
        by_cat = techs_by_category()
        for cat_key in ("LEARNING_CATEGORY", "GROWTH_CATEGORY", "PRODUCTION_CATEGORY"):
            for t in by_cat.get(cat_key, []):
                if not t.prerequisites:
                    emp.research_queue.append(t.name)
                    break
        game.empires.append(emp)
    # Ensure the player empire always exists.
    if not any(e.is_player for e in game.empires):
        if game.empires:
            game.empires[0].is_player = True
    return game


# --- top-level game object -----------------------------------------

@dataclass
class Game:
    systems: list[System] = field(default_factory=list)
    empires: list[Empire] = field(default_factory=list)
    fleets: list[Fleet] = field(default_factory=list)
    turn: int = 1
    log: list[str] = field(default_factory=list)

    # --- mutation serial (for QA and UI refresh) -------------------
    _serial: int = 0

    def bump(self) -> None:
        self._serial += 1

    @property
    def serial(self) -> int:
        return self._serial

    # --- lookups ---------------------------------------------------
    def player(self) -> Empire:
        for e in self.empires:
            if e.is_player:
                return e
        return self.empires[0]

    def empire(self, eid: int) -> Optional[Empire]:
        for e in self.empires:
            if e.id == eid:
                return e
        return None

    def system(self, sid: int) -> System:
        return self.systems[sid]

    def planets_of(self, eid: int) -> list[Planet]:
        out = []
        for s in self.systems:
            for p in s.planets:
                if p.owner == eid:
                    out.append(p)
        return out

    def fleets_of(self, eid: int) -> list[Fleet]:
        return [f for f in self.fleets if f.owner == eid]

    # --- turn advance ----------------------------------------------
    def advance_turn(self) -> list[str]:
        """Core tick: collect RP/PP, apply research, move fleets, grow pop."""
        events: list[str] = []
        self.turn += 1
        for emp in self.empires:
            # Population growth.
            for p in self.planets_of(emp.id):
                if p.population < p.max_population:
                    growth = 0.2 if p.focus == "population" else 0.1
                    p.population = min(p.max_population, p.population + growth)
                # Collect output.
                emp.rp_pool += p.research_output
                emp.pp_pool += p.industry_output
            # Baseline trickle so research always ticks even without pop.
            emp.rp_pool += 2.0
            emp.pp_pool += 1.0
            # Research — progress the head of the queue.
            self._research_tick(emp, events)
            # Production — progress head of queue.
            self._production_tick(emp, events)
        # Fleet movement.
        for f in self.fleets:
            if f.dest_id is None:
                continue
            f.eta = max(0, f.eta - 1)
            if f.eta <= 0:
                f.system_id = f.dest_id
                f.dest_id = None
                # AI empires auto-spread to adjacent unclaimed planets.
                self._auto_colonise(f, events)
        # AI empires enqueue more research.
        for emp in self.empires:
            if emp.is_player:
                continue
            if not emp.research_queue and not emp.in_progress:
                avail = emp.available_techs()
                if avail:
                    avail.sort(key=lambda t: (t.cost, t.name))
                    emp.research_queue.append(avail[0].name)
        self.log.extend(events[-12:])
        if len(self.log) > 200:
            self.log = self.log[-200:]
        self.bump()
        return events

    def _research_tick(self, emp: Empire, events: list[str]) -> None:
        if emp.in_progress is None and emp.research_queue:
            tname = emp.research_queue.pop(0)
            if tname in TECHS and tname not in emp.researched:
                emp.in_progress = ResearchProgress(tech_name=tname)
        ip = emp.in_progress
        if ip is None:
            return
        tech = TECHS.get(ip.tech_name)
        if tech is None:
            emp.in_progress = None
            return
        spend = min(emp.rp_pool, max(1.0, tech.cost / max(tech.turns, 1)))
        ip.points += spend
        emp.rp_pool = max(0.0, emp.rp_pool - spend)
        if ip.points >= tech.cost:
            emp.researched.add(tech.name)
            emp.in_progress = None
            if emp.is_player:
                events.append(f"✓ Researched [bold]{tech.short_name}[/]")

    def _production_tick(self, emp: Empire, events: list[str]) -> None:
        if not emp.production_queue:
            return
        item = emp.production_queue[0]
        spend = min(emp.pp_pool, 10.0)
        item.progress += spend
        emp.pp_pool = max(0.0, emp.pp_pool - spend)
        if item.progress >= item.cost:
            emp.production_queue.pop(0)
            if item.kind == "ship":
                # Spawn a new fleet at the build location.
                target_sys = None
                for s in self.systems:
                    for p in s.planets:
                        if p.id == item.location_id:
                            target_sys = s.id
                            break
                if target_sys is not None:
                    self.fleets.append(Fleet(
                        id=len(self.fleets), owner=emp.id,
                        name=f"{item.name} ({emp.name[:3]})",
                        system_id=target_sys, ships=1,
                    ))
                    if emp.is_player:
                        events.append(f"✓ Built {item.name}")

    def _auto_colonise(self, fleet: Fleet, events: list[str]) -> None:
        """AI-only: if a moved fleet arrived at an uncolonised habitable
        planet, claim it. Player fleets do this via explicit orders."""
        emp = self.empire(fleet.owner)
        if emp is None or emp.is_player:
            return
        sys_ = self.systems[fleet.system_id]
        for p in sys_.planets:
            if p.owner is None and p.is_habitable() and p.max_population >= 3:
                p.owner = emp.id
                p.population = 1.0
                p.focus = "industry"
                events.append(f"{emp.name} colonised {p.name}")
                return

    # --- player commands -------------------------------------------
    def enqueue_research(self, empire_id: int, tech_name: str) -> bool:
        emp = self.empire(empire_id)
        if emp is None or tech_name not in TECHS:
            return False
        if tech_name in emp.researched or tech_name in emp.research_queue:
            return False
        # Ensure prereqs are done.
        tech = TECHS[tech_name]
        if not all(p in emp.researched for p in tech.prerequisites):
            return False
        emp.research_queue.append(tech_name)
        self.bump()
        return True

    def dequeue_research(self, empire_id: int, idx: int) -> bool:
        emp = self.empire(empire_id)
        if emp is None or not (0 <= idx < len(emp.research_queue)):
            return False
        emp.research_queue.pop(idx)
        self.bump()
        return True

    def enqueue_production(self, empire_id: int, planet_id: int,
                           kind: str = "ship", name: str = "Scout") -> bool:
        emp = self.empire(empire_id)
        if emp is None:
            return False
        cost = {"ship": 40, "colony_ship": 120, "building": 80}.get(kind, 50)
        emp.production_queue.append(ProductionItem(
            kind=kind, name=name, location_id=planet_id, cost=float(cost),
        ))
        self.bump()
        return True

    def move_fleet(self, fleet_id: int, dest_system_id: int) -> bool:
        """Move a fleet to any reachable system (1 turn per lane hop)."""
        if not (0 <= fleet_id < len(self.fleets)):
            return False
        f = self.fleets[fleet_id]
        if dest_system_id == f.system_id:
            return False
        if not (0 <= dest_system_id < len(self.systems)):
            return False
        # BFS on starlanes for hop distance.
        from collections import deque
        q: deque[tuple[int, int]] = deque([(f.system_id, 0)])
        seen = {f.system_id}
        hops = -1
        while q:
            sid, d = q.popleft()
            if sid == dest_system_id:
                hops = d
                break
            for nb in self.systems[sid].starlanes:
                if nb not in seen:
                    seen.add(nb)
                    q.append((nb, d + 1))
        if hops < 0:
            return False
        f.dest_id = dest_system_id
        f.eta = max(1, hops)
        self.bump()
        return True

    # --- serialisation --------------------------------------------
    def state_snapshot(self) -> dict:
        """Compact JSON-friendly snapshot for the agent API / advisor."""
        p = self.player()
        return {
            "turn": self.turn,
            "player": {
                "id": p.id, "name": p.name,
                "rp_pool": round(p.rp_pool, 1),
                "pp_pool": round(p.pp_pool, 1),
                "researched": sorted(p.researched),
                "queue": list(p.research_queue),
                "in_progress": (
                    {"tech": p.in_progress.tech_name,
                     "points": round(p.in_progress.points, 1),
                     "required": p.in_progress.required}
                    if p.in_progress else None
                ),
                "planet_count": len(self.planets_of(p.id)),
                "fleet_count": len(self.fleets_of(p.id)),
            },
            "systems": len(self.systems),
            "log_tail": self.log[-8:],
        }


def new_game(size: int = 40, seed: Optional[int] = None,
             num_empires: int = 3) -> Game:
    """Convenience alias for ``generate_galaxy``."""
    return generate_galaxy(size=size, seed=seed, num_empires=num_empires)
