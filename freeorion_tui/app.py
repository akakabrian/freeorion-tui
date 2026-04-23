"""Textual TUI — star map + tech tree + production queue."""

from __future__ import annotations

import math
from typing import Optional

from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.geometry import Size
from textual.message import Message
from textual.reactive import reactive
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.widgets import Footer, Header, RichLog, Static

from . import content
from .engine import (
    GALAXY_HEIGHT,
    GALAXY_WIDTH,
    MAP_H,
    MAP_W,
    Fleet,
    Game,
    System,
    new_game,
)
from .screens import (
    EmpireScreen,
    GalaxyScreen,
    HelpScreen,
    LoadScreen,
    ResearchQueueScreen,
    SaveScreen,
    TechTreeScreen,
)


# ----- galaxy → grid projection -----------------------------------

def galaxy_to_grid(x: float, y: float, w: int, h: int) -> tuple[int, int]:
    """Project a (galaxy-space) coord into a character grid of ``w×h``."""
    gx = int(round(x * (w - 1) / GALAXY_WIDTH))
    gy = int(round(y * (h - 1) / GALAXY_HEIGHT))
    return max(0, min(w - 1, gx)), max(0, min(h - 1, gy))


def _heat_style(value: float, lo: float, hi: float) -> Style:
    """Red→yellow→green ramp for overlay tinting. Capped [lo, hi]."""
    span = max(1e-6, hi - lo)
    t = max(0.0, min(1.0, (value - lo) / span))
    if t < 0.5:
        # red → yellow
        r = 255
        g = int(40 + t * 2 * 200)
        b = 60
    else:
        # yellow → green
        r = int(255 - (t - 0.5) * 2 * 180)
        g = 240
        b = 80
    return Style.parse(f"bold rgb({r},{g},{b}) on rgb(4,6,14)")


# ----- map view ----------------------------------------------------

class MapView(ScrollView):
    """Renders the star map.

    Stars are placed on a chargrid using ``galaxy_to_grid``. Starlanes
    are drawn as faint lines connecting systems. The cursor is a separate
    highlight on whatever star is currently selected. Fleets appear as
    letters overlaid on their system.
    """

    DEFAULT_CSS = "MapView { padding: 0; }"

    cursor_system: reactive[int] = reactive(0)

    class SystemSelected(Message):
        def __init__(self, system_id: int) -> None:
            self.system_id = system_id
            super().__init__()

    OVERLAYS = ("none", "owners", "population", "research")

    def __init__(self, game: Game) -> None:
        super().__init__()
        self.game = game
        self.grid_w = MAP_W
        self.grid_h = MAP_H
        self.virtual_size = Size(self.grid_w, self.grid_h)
        # Overlay is a post-render tint on top of the base star map.
        self.overlay_mode = "none"
        # Pre-compute positions and a sparse lookup from grid → system_id.
        self._positions: dict[int, tuple[int, int]] = {}
        self._grid_to_system: dict[tuple[int, int], int] = {}
        self._rebuild_positions()
        # Pre-parse styles — Style.parse dominates rendering cost otherwise.
        self._star_styles: dict[str, Style] = {
            "blue":      Style.parse("bold rgb(130,180,255) on rgb(4,6,14)"),
            "white":     Style.parse("bold rgb(230,230,240) on rgb(4,6,14)"),
            "yellow":    Style.parse("bold rgb(255,230,120) on rgb(4,6,14)"),
            "orange":    Style.parse("bold rgb(255,180,90) on rgb(8,5,10)"),
            "red":       Style.parse("bold rgb(255,110,90) on rgb(10,4,8)"),
            "neutron":   Style.parse("bold rgb(200,220,255) on rgb(4,6,14)"),
            "black_hole": Style.parse("bold rgb(180,80,220) on rgb(4,0,8)"),
        }
        self._lane_style = Style.parse("rgb(60,70,110) on rgb(4,6,14)")
        self._empty_style = Style.parse("rgb(18,22,40) on rgb(4,6,14)")
        self._cursor_style = Style.parse(
            "bold black on rgb(255,220,80)"
        )
        self._empire_highlight: dict[int, Style] = {}
        # Fleet glyph alternates to animate movement.
        self._anim_frame = 0
        # Cache the last serial we rendered at.
        self._last_serial = -1
        self.set_styles_for_empires()

    def _rebuild_positions(self) -> None:
        self._positions.clear()
        self._grid_to_system.clear()
        # If two systems land on the same cell, nudge the second one.
        used: set[tuple[int, int]] = set()
        for s in self.game.systems:
            gx, gy = galaxy_to_grid(s.x, s.y, self.grid_w, self.grid_h)
            # Tiny nudge to resolve collisions.
            for dx, dy in [(0, 0), (1, 0), (0, 1), (-1, 0), (0, -1), (1, 1), (-1, -1)]:
                nx, ny = gx + dx, gy + dy
                if (nx, ny) not in used and 0 <= nx < self.grid_w and 0 <= ny < self.grid_h:
                    gx, gy = nx, ny
                    break
            used.add((gx, gy))
            self._positions[s.id] = (gx, gy)
            self._grid_to_system[(gx, gy)] = s.id

    def set_styles_for_empires(self) -> None:
        for emp in self.game.empires:
            r, g, b = emp.color
            # Darker tint used as background highlight for owned systems.
            bg = f"rgb({r // 4},{g // 4},{b // 4})"
            self._empire_highlight[emp.id] = Style.parse(
                f"bold rgb({r},{g},{b}) on {bg}"
            )

    # --- rendering ---------------------------------------------------

    def _line_chars_between(
        self, x0: int, y0: int, x1: int, y1: int
    ) -> list[tuple[int, int]]:
        """Cheap Bresenham — returns the set of grid cells on the line
        strictly between the endpoints (so we don't overwrite stars)."""
        cells: list[tuple[int, int]] = []
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        x, y = x0, y0
        while True:
            if (x, y) != (x0, y0) and (x, y) != (x1, y1):
                cells.append((x, y))
            if x == x1 and y == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x += sx
            if e2 <= dx:
                err += dx
                y += sy
        return cells

    def _compute_lanes(self) -> dict[tuple[int, int], str]:
        """Map each 'between-star' cell to a lane glyph based on slope."""
        lanes: dict[tuple[int, int], str] = {}
        seen: set[tuple[int, int]] = set()
        for s in self.game.systems:
            x0, y0 = self._positions[s.id]
            for other_id in s.starlanes:
                pair = (min(s.id, other_id), max(s.id, other_id))
                if pair in seen:
                    continue
                seen.add(pair)
                x1, y1 = self._positions[other_id]
                dx = x1 - x0
                dy = y1 - y0
                if dx == 0:
                    glyph = "│"
                elif dy == 0:
                    glyph = "─"
                elif dx * dy > 0:
                    glyph = "╲"
                else:
                    glyph = "╱"
                for (cx, cy) in self._line_chars_between(x0, y0, x1, y1):
                    # Don't overwrite another lane — let the first one win.
                    lanes.setdefault((cx, cy), glyph)
        return lanes

    def render_line(self, y: int) -> Strip:
        scroll_x, scroll_y = self.scroll_offset
        grid_y = y + int(scroll_y)
        width = self.size.width
        if grid_y < 0 or grid_y >= self.grid_h:
            return Strip.blank(width)

        # (Re)compute lane layer — cached between serials for speed.
        if not hasattr(self, "_lane_cache") or self._last_serial != self.game.serial:
            self._lane_cache = self._compute_lanes()
            # Build reverse index for fast owner lookups.
            self._owner_at: dict[tuple[int, int], int] = {}
            for s in self.game.systems:
                owner = s.owner
                if owner is not None:
                    self._owner_at[self._positions[s.id]] = owner
            # Fleet overlay — which cells have a fleet this frame, and whose.
            self._fleet_at: dict[tuple[int, int], list[Fleet]] = {}
            for f in self.game.fleets:
                pos = self._positions.get(f.system_id)
                if pos is not None:
                    self._fleet_at.setdefault(pos, []).append(f)
            self._last_serial = self.game.serial

        lanes = self._lane_cache
        start_x = max(0, int(scroll_x))
        end_x = min(self.grid_w, start_x + width)

        cursor_pos = self._positions.get(self.cursor_system)
        segments: list[Segment] = []
        run_chars: list[str] = []
        run_style: Optional[Style] = None
        for x in range(start_x, end_x):
            glyph = " "
            style = self._empty_style
            is_cursor = cursor_pos is not None and (x, grid_y) == cursor_pos
            sid = self._grid_to_system.get((x, grid_y))
            if sid is not None:
                s = self.game.systems[sid]
                glyph = s.star_glyph
                # Owner tint takes precedence over raw star type.
                owner = self._owner_at.get((x, grid_y))
                if owner is not None:
                    style = self._empire_highlight.get(
                        owner, self._star_styles.get(s.star_type, self._empty_style)
                    )
                else:
                    style = self._star_styles.get(s.star_type, self._empty_style)
                # Fleet overlay: letter on the star.
                fleets_here = self._fleet_at.get((x, grid_y))
                if fleets_here:
                    # First own fleet first (if any) for readable highlight.
                    f = next((ff for ff in fleets_here
                              if ff.owner == self.game.player().id),
                             fleets_here[0])
                    glyph = "►" if f.owner == self.game.player().id else "◄"
                    style = self._empire_highlight.get(f.owner, style)
                # Data overlays — tint the star by a metric.
                if self.overlay_mode != "none":
                    ovr = self._overlay_style_for(s)
                    if ovr is not None:
                        style = ovr
            elif (x, grid_y) in lanes:
                glyph = lanes[(x, grid_y)]
                style = self._lane_style
            if is_cursor:
                style = self._cursor_style
            if style is run_style:
                run_chars.append(glyph)
            else:
                if run_chars:
                    segments.append(Segment("".join(run_chars), run_style))
                run_chars = [glyph]
                run_style = style
        if run_chars:
            segments.append(Segment("".join(run_chars), run_style))
        visible = end_x - start_x
        if visible < width:
            segments.append(Segment(" " * (width - visible)))
        return Strip(segments, width)

    # --- overlay tinting --------------------------------------------
    def _overlay_style_for(self, s: "System") -> Optional[Style]:
        """Translate a data metric on system ``s`` into a heat-style.

        Owners → empire's own bright-on-dark style (same as default owner
        tint, but extended to every owned star even when the cursor/fleet
        glyph would otherwise hide it). Population / research use a
        red→yellow→green ramp."""
        if self.overlay_mode == "owners":
            owner = s.owner
            if owner is None:
                return Style.parse("rgb(30,35,60) on rgb(4,6,14)")
            return self._empire_highlight.get(owner)
        if self.overlay_mode == "population":
            pop = sum(p.population for p in s.planets)
            return _heat_style(pop, 0.0, 30.0)
        if self.overlay_mode == "research":
            # Aggregate research output potential on system's planets.
            r = sum(max(p.research_output, p.max_population * 0.3)
                    for p in s.planets if p.is_habitable())
            return _heat_style(r, 0.0, 15.0)
        return None

    def cycle_overlay(self) -> str:
        idx = self.OVERLAYS.index(self.overlay_mode)
        self.overlay_mode = self.OVERLAYS[(idx + 1) % len(self.OVERLAYS)]
        # Invalidate cached render state.
        self._last_serial = -1
        self.refresh()
        return self.overlay_mode

    # --- cursor movement --------------------------------------------
    def move_cursor_to_nearest(self, dx: int, dy: int) -> None:
        """Jump to the nearest system in direction (dx, dy).

        Used by arrow keys. We don't do free-cursor movement — the map
        is too sparse; jumping between stars is the FreeOrion feel."""
        if not self.game.systems:
            return
        cur = self.game.systems[self.cursor_system]
        best_id = None
        best_score = float("inf")
        for s in self.game.systems:
            if s.id == cur.id:
                continue
            vx = s.x - cur.x
            vy = s.y - cur.y
            # Filter to systems roughly in the requested direction.
            if dx > 0 and vx <= 0:
                continue
            if dx < 0 and vx >= 0:
                continue
            if dy > 0 and vy <= 0:
                continue
            if dy < 0 and vy >= 0:
                continue
            # Score: Euclidean distance, lightly favouring on-axis.
            axis_penalty = 0.0
            if dx != 0:
                axis_penalty += abs(vy)
            if dy != 0:
                axis_penalty += abs(vx)
            score = math.hypot(vx, vy) + axis_penalty * 0.3
            if score < best_score:
                best_score = score
                best_id = s.id
        if best_id is not None:
            self.cursor_system = best_id

    def watch_cursor_system(self, old: int, new: int) -> None:
        if not self.is_mounted:
            return
        self.refresh()
        self.post_message(self.SystemSelected(new))

    # --- mouse --------------------------------------------------------
    def on_click(self, event: events.Click) -> None:
        gx = event.x + int(self.scroll_offset.x)
        gy = event.y + int(self.scroll_offset.y)
        sid = self._grid_to_system.get((gx, gy))
        if sid is not None:
            self.cursor_system = sid


# ----- side panels ------------------------------------------------

class StatusPanel(Static):
    """Turn counter, player RP/PP, current research progress."""

    def __init__(self, game: Game) -> None:
        super().__init__()
        self.game = game
        self.border_title = "EMPIRE"
        self._last: tuple | None = None

    def refresh_panel(self) -> None:
        if not self.is_mounted:
            return
        p = self.game.player()
        ip = p.in_progress
        ip_name = ip.tech_name if ip is not None else None
        ip_pct = (
            int(100 * ip.points / content.TECHS[ip_name].cost)
            if ip is not None and ip_name in content.TECHS else 0
        )
        sig = (self.game.turn, p.id, round(p.rp_pool, 1), round(p.pp_pool, 1),
               ip_name, ip_pct, len(p.researched), len(p.research_queue),
               len(self.game.planets_of(p.id)))
        if sig == self._last:
            return
        self._last = sig
        t = Text()
        r, g, b = p.color
        t.append(f"{p.name}\n", style=f"bold rgb({r},{g},{b})")
        t.append(f"Turn: {self.game.turn}\n")
        t.append(f"Research Pool: {p.rp_pool:.1f} RP\n")
        t.append(f"Industry Pool: {p.pp_pool:.1f} PP\n")
        t.append(f"Planets: {len(self.game.planets_of(p.id))}  "
                 f"Fleets: {len(self.game.fleets_of(p.id))}\n")
        t.append(f"Researched: {len(p.researched)}/{len(content.TECHS)}\n\n")
        if ip is not None and ip_name is not None:
            tech = content.TECHS[ip_name]
            t.append("Researching:\n", style="bold")
            t.append(f"  {tech.short_name}\n")
            cat = content.CATEGORIES.get(tech.category)
            cat_style = (
                f"rgb({cat.color[0]},{cat.color[1]},{cat.color[2]})"
                if cat else "white"
            )
            t.append(f"  [{cat.short_name if cat else tech.category}] ",
                     style=cat_style)
            t.append(f"{ip_pct}%\n")
            # Progress bar.
            bar_w = 20
            filled = int(bar_w * ip.points / tech.cost)
            t.append("  " + "█" * filled + "░" * (bar_w - filled) + "\n",
                     style=cat_style)
        else:
            t.append("No active research.\n", style="dim")
        self.update(t)


class TechPanel(Static):
    """Browsable tech tree — collapsed by category. Enter queues the
    highlighted tech for research."""

    class Queue(Message):
        def __init__(self, tech_name: str) -> None:
            self.tech_name = tech_name
            super().__init__()

    def __init__(self, game: Game) -> None:
        super().__init__()
        self.game = game
        self.border_title = "TECHS"
        self.cursor_idx = 0
        self._flat: list[tuple[str, str]] = []  # (kind, key) where kind ∈ {"cat","tech"}
        self.expanded: set[str] = {"LEARNING_CATEGORY", "GROWTH_CATEGORY",
                                   "PRODUCTION_CATEGORY"}
        self._rebuild()

    def _rebuild(self) -> None:
        self._flat = []
        by_cat = content.techs_by_category()
        # Sort categories in a stable order — important categories first.
        order = ["LEARNING_CATEGORY", "GROWTH_CATEGORY", "PRODUCTION_CATEGORY",
                 "CONSTRUCTION_CATEGORY", "DEFENSE_CATEGORY",
                 "SHIP_HULLS_CATEGORY", "SHIP_WEAPONS_CATEGORY",
                 "SHIP_PARTS_CATEGORY", "SPY_CATEGORY"]
        ordered = [c for c in order if c in by_cat] + [
            c for c in by_cat if c not in order
        ]
        for cat_name in ordered:
            self._flat.append(("cat", cat_name))
            if cat_name in self.expanded:
                for t in by_cat[cat_name]:
                    self._flat.append(("tech", t.name))

    def move(self, delta: int) -> None:
        if not self._flat:
            return
        self.cursor_idx = max(0, min(len(self._flat) - 1, self.cursor_idx + delta))
        self.refresh_panel()

    def toggle(self) -> None:
        """Enter on a category toggles expand; on a tech it queues research."""
        if not self._flat:
            return
        kind, key = self._flat[self.cursor_idx]
        if kind == "cat":
            if key in self.expanded:
                self.expanded.remove(key)
            else:
                self.expanded.add(key)
            self._rebuild()
            self.cursor_idx = min(self.cursor_idx, len(self._flat) - 1)
            self.refresh_panel()
        else:
            self.post_message(self.Queue(key))

    def refresh_panel(self) -> None:
        if not self.is_mounted:
            return
        p = self.game.player()
        t = Text()
        by_cat = content.techs_by_category()
        # Visible window — the panel is short; we show a slice around the cursor.
        height = max(4, self.size.height - 2)
        if height <= 0:
            height = 20
        start = max(0, self.cursor_idx - height // 2)
        end = min(len(self._flat), start + height)
        for i in range(start, end):
            kind, key = self._flat[i]
            is_cursor = (i == self.cursor_idx)
            prefix = "▶ " if is_cursor else "  "
            if kind == "cat":
                cat = content.CATEGORIES.get(key)
                total = len(by_cat.get(key, []))
                done = sum(1 for tt in by_cat.get(key, []) if tt.name in p.researched)
                caret = "▼" if key in self.expanded else "▶"
                t.append(prefix + caret + " ",
                         style="bold reverse" if is_cursor else "bold")
                if cat:
                    r, g, b = cat.color
                    t.append(f"{cat.short_name}",
                             style=f"bold rgb({r},{g},{b})")
                else:
                    t.append(key, style="bold")
                t.append(f"  {done}/{total}\n", style="dim")
            else:
                tech = content.TECHS.get(key)
                if tech is None:
                    continue
                status = " "
                style = ""
                if tech.name in p.researched:
                    status = "✓"
                    style = "green"
                elif tech.name in p.research_queue:
                    status = "…"
                    style = "yellow"
                elif p.in_progress and p.in_progress.tech_name == tech.name:
                    status = "►"
                    style = "bold cyan"
                elif all(pr in p.researched for pr in tech.prerequisites):
                    status = "·"
                else:
                    status = "✗"
                    style = "dim"
                # Short name truncated to 24 chars.
                name = tech.short_name
                if len(name) > 22:
                    name = name[:21] + "…"
                label = f"    {status} {name:<22} {tech.cost:>3}RP"
                if is_cursor:
                    t.append(prefix + label + "\n",
                             style=("bold reverse " + style).strip())
                else:
                    t.append("  " + label + "\n", style=style)
        self.update(t)


class QueuePanel(Static):
    """Research + production queues side by side."""

    def __init__(self, game: Game) -> None:
        super().__init__()
        self.game = game
        self.border_title = "QUEUES"
        self._last: tuple | None = None

    def refresh_panel(self) -> None:
        if not self.is_mounted:
            return
        p = self.game.player()
        rq = list(p.research_queue)
        pq = list(p.production_queue)
        sig = (tuple(rq), tuple((it.kind, it.name, round(it.progress, 1))
                                for it in pq))
        if sig == self._last:
            return
        self._last = sig
        t = Text()
        t.append("Research\n", style="bold")
        if not rq:
            t.append("  (empty)\n", style="dim")
        for i, name in enumerate(rq[:6]):
            tech = content.TECHS.get(name)
            short = tech.short_name if tech else name
            cost = f"{tech.cost}RP" if tech else ""
            t.append(f"  {i+1}. {short:<20} {cost}\n")
        if len(rq) > 6:
            t.append(f"  + {len(rq) - 6} more\n", style="dim")
        t.append("\nProduction\n", style="bold")
        if not pq:
            t.append("  (empty)\n", style="dim")
        for i, it in enumerate(pq[:5]):
            t.append(f"  {i+1}. {it.name:<14} {it.percent:>3}%\n")
        self.update(t)


class FleetPanel(Static):
    """Info on the selected system: star + planets + fleets there."""

    def __init__(self, game: Game) -> None:
        super().__init__()
        self.game = game
        self.border_title = "SYSTEM"
        self.system_id = 0
        self._last: tuple | None = None

    def set_system(self, sid: int) -> None:
        self.system_id = sid
        if self.is_mounted:
            self.refresh_panel()

    def refresh_panel(self) -> None:
        if not self.is_mounted:
            return
        s = self.game.system(self.system_id)
        fleets_here = [f for f in self.game.fleets if f.system_id == self.system_id]
        sig = (
            s.id, s.owner, len(fleets_here),
            tuple((p.id, p.owner, round(p.population, 1), p.focus) for p in s.planets),
            tuple((f.id, f.owner, f.dest_id, f.eta) for f in fleets_here),
        )
        if sig == self._last:
            return
        self._last = sig
        t = Text()
        t.append(f"{s.name}  [{s.star_type} star]\n", style="bold")
        if s.planets:
            t.append("Planets:\n", style="bold")
            for p in s.planets:
                owner = ""
                if p.owner is not None:
                    emp = self.game.empire(p.owner)
                    if emp:
                        r, g, b = emp.color
                        owner = f" [{emp.name[:12]}]"
                        t.append(f"  {p.symbol} {p.name:<18} "
                                 f"pop {p.population:>4.1f}/{p.max_population:<4.1f} "
                                 f"[{p.focus[:4]:<4}]", style=f"rgb({r},{g},{b})")
                        t.append(owner + "\n")
                        continue
                t.append(f"  {p.symbol} {p.name:<18} "
                         f"pop {p.population:>4.1f}/{p.max_population:<4.1f} "
                         f"[{p.type}]\n")
        else:
            t.append("No planets.\n", style="dim")
        if fleets_here:
            t.append("Fleets:\n", style="bold")
            for f in fleets_here:
                emp = self.game.empire(f.owner)
                r, g, b = emp.color if emp else (200, 200, 200)
                dest = ""
                if f.dest_id is not None:
                    dest = f" → {self.game.system(f.dest_id).name} (ETA {f.eta})"
                t.append(f"  #{f.id} {f.name[:20]}  x{f.ships}{dest}\n",
                         style=f"rgb({r},{g},{b})")
        else:
            t.append("No fleets.\n", style="dim")
        self.update(t)


# ----- app ---------------------------------------------------------

class FreeOrionApp(App):
    CSS_PATH = "tui.tcss"
    TITLE = "FreeOrion — Terminal"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("space", "end_turn", "End Turn", priority=True),
        Binding("t", "focus_techs", "Techs"),
        Binding("m", "focus_map", "Map"),
        Binding("f", "build_fleet", "Build"),
        Binding("p", "change_focus", "Focus"),
        Binding("o", "cycle_overlay", "Overlay"),
        Binding("g", "go_fleet", "Move"),
        Binding("c", "colonise", "Colonise"),
        Binding("question_mark", "help", "Help"),
        Binding("G", "galaxy_screen", "Galaxy"),
        Binding("T", "tech_tree", "TechTree"),
        Binding("E", "empire_screen", "Empires"),
        Binding("R", "research_queue", "Queue"),
        Binding("S", "save_game", "Save"),
        Binding("L", "load_game", "Load"),
        Binding("up",    "move_cursor(0,-1)", "↑", show=False, priority=True),
        Binding("down",  "move_cursor(0,1)",  "↓", show=False, priority=True),
        Binding("left",  "move_cursor(-1,0)", "←", show=False, priority=True),
        Binding("right", "move_cursor(1,0)",  "→", show=False, priority=True),
        Binding("k", "tech_up", "", show=False, priority=True),
        Binding("j", "tech_down", "", show=False, priority=True),
        Binding("enter", "tech_activate", "Queue/Expand", priority=True),
    ]

    paused: reactive[bool] = reactive(False)
    focus_mode: reactive[str] = reactive("map")  # "map" | "techs"

    def __init__(self, *, seed: int | None = None, galaxy_size: int = 40,
                 agent_port: int | None = None) -> None:
        super().__init__()
        self.game = new_game(size=galaxy_size, seed=seed, num_empires=3)
        self._agent_port = agent_port
        # Cursor starts on the player's home system.
        player = self.game.player()
        self.map_view = MapView(self.game)
        self.map_view.cursor_system = player.home_system_id
        self.status_panel = StatusPanel(self.game)
        self.tech_panel = TechPanel(self.game)
        self.queue_panel = QueuePanel(self.game)
        self.fleet_panel = FleetPanel(self.game)
        self.fleet_panel.system_id = player.home_system_id
        self.message_log = RichLog(
            id="log", highlight=False, markup=True, wrap=False, max_lines=300,
        )
        self.message_log.border_title = "EVENTS"
        self.flash_bar = Static(" ", id="flash-bar")
        self._flash_timer = None

    # --- layout ------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="body"):
            with Vertical(id="map-col"):
                yield self.map_view
                yield self.flash_bar
                yield self.message_log
            with Vertical(id="side"):
                yield self.status_panel
                yield self.fleet_panel
                yield self.tech_panel
                yield self.queue_panel
        yield Footer()

    # --- lifecycle ---------------------------------------------------
    async def on_mount(self) -> None:
        p = self.game.player()
        self.map_view.border_title = (
            f"Galaxy · {len(self.game.systems)} systems · "
            f"{len(self.game.empires)} empires"
        )
        self._refresh_panels()
        self.update_header()
        self.log_msg(f"[bold]Welcome, Commander of the {p.name}.[/]")
        self.log_msg("Move with arrow keys · [bold]Space[/] ends turn · "
                     "[bold]t[/] browses techs · [bold]?[/] for help")
        if self._agent_port is not None:
            from .agent_api import start_server
            self._agent_runner = await start_server(self, port=self._agent_port)
            self.log_msg(f"[cyan]agent API on http://127.0.0.1:{self._agent_port}[/]")

    def _refresh_panels(self) -> None:
        self.status_panel.refresh_panel()
        self.tech_panel.refresh_panel()
        self.queue_panel.refresh_panel()
        self.fleet_panel.refresh_panel()

    def update_header(self) -> None:
        p = self.game.player()
        self.sub_title = (
            f"Turn {self.game.turn}  ·  {p.name}  ·  "
            f"{p.rp_pool:.0f} RP  ·  {p.pp_pool:.0f} PP  ·  "
            f"{len(p.researched)} tech"
        )
        s = self.game.system(self.map_view.cursor_system)
        owner = ""
        if s.owner is not None:
            emp = self.game.empire(s.owner)
            if emp:
                owner = f"  owned by [bold]{emp.name}[/]"
        self.map_view.border_title = (
            f"Galaxy · cursor {s.name}{owner}"
        )

    # --- actions -----------------------------------------------------
    def action_end_turn(self) -> None:
        events = self.game.advance_turn()
        for e in events[-5:]:
            self.log_msg(e)
        self._refresh_panels()
        self.update_header()
        self.flash_status(f"[green]▶ Turn {self.game.turn} begins[/]")

    def action_move_cursor(self, dx: str, dy: str) -> None:
        ddx, ddy = int(dx), int(dy)
        if self.focus_mode == "techs":
            # In tech mode, up/down drive tech cursor; left/right collapse.
            if ddy != 0:
                self.tech_panel.move(ddy)
            elif ddx < 0:
                # Collapse if expanded, else move to category header.
                kind, key = self.tech_panel._flat[self.tech_panel.cursor_idx]
                if kind == "tech":
                    # Find the parent category idx.
                    for i in range(self.tech_panel.cursor_idx, -1, -1):
                        if self.tech_panel._flat[i][0] == "cat":
                            self.tech_panel.cursor_idx = i
                            break
                elif key in self.tech_panel.expanded:
                    self.tech_panel.expanded.discard(key)
                    self.tech_panel._rebuild()
                self.tech_panel.refresh_panel()
            elif ddx > 0:
                kind, key = self.tech_panel._flat[self.tech_panel.cursor_idx]
                if kind == "cat" and key not in self.tech_panel.expanded:
                    self.tech_panel.expanded.add(key)
                    self.tech_panel._rebuild()
                    self.tech_panel.refresh_panel()
            return
        # Map mode — jump to nearest star in direction.
        self.map_view.move_cursor_to_nearest(ddx, ddy)
        self.update_header()

    def action_tech_up(self) -> None:
        if self.focus_mode == "techs":
            self.tech_panel.move(-1)

    def action_tech_down(self) -> None:
        if self.focus_mode == "techs":
            self.tech_panel.move(1)

    def action_tech_activate(self) -> None:
        if self.focus_mode == "techs":
            self.tech_panel.toggle()

    def action_focus_techs(self) -> None:
        self.focus_mode = "techs"
        self.flash_status("[yellow]Tech browser[/] · ↑↓ navigate · Enter queues · m returns")
        self.tech_panel.refresh_panel()

    def action_focus_map(self) -> None:
        self.focus_mode = "map"
        self.flash_status("[cyan]Map mode[/] · arrows jump between stars")

    def action_build_fleet(self) -> None:
        """Queue a Scout build at the first owned planet in the cursor system."""
        p = self.game.player()
        sys_ = self.game.system(self.map_view.cursor_system)
        owned = [pl for pl in sys_.planets if pl.owner == p.id]
        if not owned:
            self.flash_status("[red]✗ you don't own a planet here[/]")
            return
        self.game.enqueue_production(p.id, owned[0].id, kind="ship", name="Scout")
        self.flash_status(f"[green]✓ queued Scout at {owned[0].name}[/]")
        self.queue_panel.refresh_panel()

    def action_change_focus(self) -> None:
        """Cycle the focus on all owned planets in the current system."""
        p = self.game.player()
        sys_ = self.game.system(self.map_view.cursor_system)
        owned = [pl for pl in sys_.planets if pl.owner == p.id]
        if not owned:
            self.flash_status("[red]✗ no owned planets here[/]")
            return
        cycle = ["research", "industry", "population"]
        changed = 0
        for pl in owned:
            try:
                i = cycle.index(pl.focus)
            except ValueError:
                i = -1
            pl.focus = cycle[(i + 1) % len(cycle)]
            changed += 1
        self.game.bump()
        self.fleet_panel.refresh_panel()
        self.flash_status(f"[green]✓ cycled focus on {changed} planet(s)[/]")

    def action_go_fleet(self) -> None:
        """Send the player's first fleet at the cursor system toward the
        previously cursor-selected system. Minimal but functional."""
        p = self.game.player()
        target = self.map_view.cursor_system
        # Pick the first player fleet not already en route.
        fleets = [f for f in self.game.fleets_of(p.id) if f.dest_id is None]
        if not fleets:
            self.flash_status("[red]✗ no idle fleets[/]")
            return
        f = fleets[0]
        if f.system_id == target:
            self.flash_status("[yellow]fleet is already here[/]")
            return
        if self.game.move_fleet(f.id, target):
            dest = self.game.system(target).name
            self.flash_status(
                f"[green]✓ fleet #{f.id} → {dest} (ETA {f.eta})[/]"
            )
            self.fleet_panel.refresh_panel()
        else:
            self.flash_status("[red]✗ no route[/]")

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_cycle_overlay(self) -> None:
        mode = self.map_view.cycle_overlay()
        label = {
            "none": "[dim]no overlay[/]",
            "owners": "[cyan]overlay:[/] empire control",
            "population": "[cyan]overlay:[/] planet population",
            "research": "[cyan]overlay:[/] research potential",
        }.get(mode, mode)
        self.flash_status(label)

    def action_colonise(self) -> None:
        """Plant a colony on the best unowned habitable planet at cursor."""
        p = self.game.player()
        sys_ = self.game.system(self.map_view.cursor_system)
        # Check there's a player fleet at the system (rough colony-ship).
        fleets = [f for f in self.game.fleets
                  if f.system_id == sys_.id and f.owner == p.id]
        if not fleets:
            self.flash_status("[red]✗ no fleet at this system[/]")
            return
        targets = [pl for pl in sys_.planets
                   if pl.owner is None and pl.is_habitable()
                   and pl.max_population >= 3]
        if not targets:
            self.flash_status("[red]✗ no colonisable planets here[/]")
            return
        best = max(targets, key=lambda pl: pl.max_population)
        best.owner = p.id
        best.population = 1.0
        best.focus = "industry"
        self.game.bump()
        self.fleet_panel.refresh_panel()
        self.status_panel.refresh_panel()
        self.flash_status(f"[green]✓ colonised {best.name}[/]")
        self.log_msg(f"Colonised [bold]{best.name}[/] ({best.type})")

    def action_galaxy_screen(self) -> None:
        self.push_screen(GalaxyScreen(self.game))

    def action_tech_tree(self) -> None:
        self.push_screen(TechTreeScreen(self.game))

    def action_empire_screen(self) -> None:
        self.push_screen(EmpireScreen(self.game))

    def action_research_queue(self) -> None:
        # Use a callback to refresh side panels when the user closes.
        def _after(_result=None) -> None:
            self.queue_panel.refresh_panel()
            self.tech_panel.refresh_panel()
        self.push_screen(ResearchQueueScreen(self.game), _after)

    def action_save_game(self) -> None:
        self.push_screen(SaveScreen(self.game))

    def action_load_game(self) -> None:
        def _after(game) -> None:
            if game is None:
                return
            self.game = game
            # Rebuild widgets bound to the old Game.
            self.map_view.game = game
            self.map_view._rebuild_positions()
            self.map_view.set_styles_for_empires()
            self.map_view._last_serial = -1
            p = game.player()
            self.map_view.cursor_system = p.home_system_id
            self.map_view.refresh()
            self.status_panel.game = game
            self.status_panel._last = None
            self.tech_panel.game = game
            self.queue_panel.game = game
            self.queue_panel._last = None
            self.fleet_panel.game = game
            self.fleet_panel.system_id = p.home_system_id
            self.fleet_panel._last = None
            self._refresh_panels()
            self.update_header()
            self.log_msg(f"[cyan]✓ loaded save at turn {game.turn}[/]")
        self.push_screen(LoadScreen(), _after)

    # --- messages from widgets --------------------------------------
    def on_map_view_system_selected(self, msg: MapView.SystemSelected) -> None:
        self.fleet_panel.set_system(msg.system_id)
        self.update_header()

    def on_tech_panel_queue(self, msg: TechPanel.Queue) -> None:
        p = self.game.player()
        if self.game.enqueue_research(p.id, msg.tech_name):
            tech = content.TECHS[msg.tech_name]
            self.flash_status(f"[green]✓ queued {tech.short_name}[/]")
            self.queue_panel.refresh_panel()
            self.tech_panel.refresh_panel()
        else:
            self.flash_status("[red]✗ can't queue (already done / prereq missing)[/]")

    # --- logging helpers --------------------------------------------
    def log_msg(self, msg: str) -> None:
        self.message_log.write(f"[dim][T{self.game.turn}][/] {msg}")

    def flash_status(self, msg: str, seconds: float = 2.0) -> None:
        self.flash_bar.update(Text.from_markup(msg))
        if self._flash_timer is not None:
            self._flash_timer.stop()

        def _clear():
            self._flash_timer = None
            self.flash_bar.update(" ")
        self._flash_timer = self.set_timer(seconds, _clear)


# ----- run helper -------------------------------------------------

def run(seed: int | None = None, galaxy_size: int = 40,
        agent_port: int | None = None, headless: bool = False) -> None:
    if headless:
        import asyncio
        from .agent_api import start_server
        if agent_port is None:
            agent_port = 8789
        app = FreeOrionApp(seed=seed, galaxy_size=galaxy_size, agent_port=agent_port)

        async def _main() -> None:
            runner = await start_server(app, port=agent_port)
            print(f"[freeorion-tui] headless, agent API on "
                  f"http://127.0.0.1:{agent_port}")
            try:
                while True:
                    await asyncio.sleep(5.0)  # don't auto-advance in headless
            finally:
                await runner.cleanup()
        try:
            asyncio.run(_main())
        except KeyboardInterrupt:
            pass
        return
    app = FreeOrionApp(seed=seed, galaxy_size=galaxy_size, agent_port=agent_port)
    try:
        app.run()
    finally:
        import sys
        sys.stdout.write(
            "\033[?1000l\033[?1002l\033[?1003l\033[?1006l\033[?1015l\033[?25h"
        )
        sys.stdout.flush()
