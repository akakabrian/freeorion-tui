"""Modal screens + overlay helpers for freeorion-tui.

Separated from ``app.py`` so the core App stays focused on wiring. Each
screen reads directly from the live ``Game``; nothing is pre-serialised.

Dialogs use **non-priority** keys (letters, `+`/`-`, `q`, `escape`) so they
don't fight the App's priority arrow / enter bindings from the map layer.
"""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from . import content
from .engine import Empire, Game


# Save data lives under the user's local state dir so we don't litter CWD.
SAVE_DIR = Path.home() / ".local" / "share" / "freeorion-tui" / "saves"


# ---------- shared helpers --------------------------------------------------


def _sparkline(values: list[float], width: int = 40) -> str:
    """8-level block sparkline scaled to the window's min/max."""
    if not values:
        return " " * width
    if len(values) > width:
        step = len(values) / width
        values = [values[int(i * step)] for i in range(width)]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    blocks = "▁▂▃▄▅▆▇█"
    out = []
    for v in values:
        idx = int(((v - lo) / span) * (len(blocks) - 1))
        out.append(blocks[max(0, min(len(blocks) - 1, idx))])
    return "".join(out).ljust(width)


def _bar(value: float, maximum: float, width: int = 20, glyph: str = "█") -> str:
    if maximum <= 0:
        return " " * width
    n = max(0, min(width, int((value / maximum) * width)))
    return glyph * n + "·" * (width - n)


# ---------- help ------------------------------------------------------------


HELP_TEXT = """[bold cyan]FreeOrion — Terminal[/]

[bold #f0c080]MAP MODE[/]
  ↑ ↓ ← →             Jump between stars in that direction
  Space               End turn
  m                   Focus map  ·  t   Focus tech browser
  f                   Build Scout at cursor system
  c                   Colonise habitable planet at cursor
  o                   Cycle map overlay (none → owners → pop → research)
  g                   Send idle fleet to cursor system
  q                   Quit  ·  ?   This help

[bold #f0c080]TECH MODE[/]
  ↑ ↓                 Navigate  ·  ← →   Collapse / expand category
  Enter               Queue tech for research (or expand category)

[bold #f0c080]DIALOGS[/]
  G                   Galaxy overview (all systems)
  T                   Full-screen tech tree
  E                   Empire comparison
  R                   Research queue editor
  S                   Save game  ·  L   Load game

[bold #f0c080]SYMBOLS[/]
  ✦ ✧ ★ ☆ ✯           Stars (blue / white / yellow / orange / red)
  ● = black hole · ✴ = neutron star
  ► ◄                 Your fleet / rival fleet
  ─ │ ╲ ╱             Starlanes
  ✓ · ✗ …             Tech: done / available / blocked / queued

Press [bold]escape[/] or [bold]?[/] to close.
"""


class HelpScreen(ModalScreen):
    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
    }
    HelpScreen > Static {
        width: 80;
        height: auto;
        border: round #8a9cd2;
        background: #0b0f20;
        padding: 1 2;
    }
    """
    BINDINGS = [Binding("escape,q,question_mark", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        yield Static(Text.from_markup(HELP_TEXT))


# ---------- galaxy overview -------------------------------------------------


class GalaxyScreen(ModalScreen):
    """List every system, its owner, planet count, and fleet presence."""

    DEFAULT_CSS = """
    GalaxyScreen {
        align: center middle;
    }
    GalaxyScreen > Static {
        width: 96;
        height: 32;
        border: round #8a9cd2;
        background: #0b0f20;
        padding: 1 2;
    }
    """
    BINDINGS = [Binding("escape,q", "dismiss", "Close")]

    def __init__(self, game: Game) -> None:
        super().__init__()
        self.game = game

    def compose(self) -> ComposeResult:
        yield Static(self._render_body(), id="galaxy-body")

    def _render_body(self) -> Text:
        t = Text()
        t.append("GALAXY OVERVIEW", style="bold cyan")
        t.append(f"   Turn {self.game.turn}\n\n", style="dim")
        t.append(f"{'#':<4}{'System':<14}{'Star':<10}{'Owner':<22}"
                 f"{'Pl':>3}{'Pop':>8}{'Fleets':>8}\n",
                 style="bold")
        t.append("─" * 72 + "\n", style="dim")
        for s in self.game.systems:
            owner_name = "—"
            owner_style = "dim"
            if s.owner is not None:
                emp = self.game.empire(s.owner)
                if emp is not None:
                    owner_name = emp.name[:20]
                    r, g, b = emp.color
                    owner_style = f"rgb({r},{g},{b})"
            pop = sum(p.population for p in s.planets)
            fleets_here = sum(1 for f in self.game.fleets
                              if f.system_id == s.id)
            line = (
                f"{s.id:<4}{s.name[:13]:<14}{s.star_type:<10}"
                f"{owner_name:<22}{len(s.planets):>3}{pop:>8.1f}{fleets_here:>8}\n"
            )
            t.append(line, style=owner_style)
        return t


# ---------- empire comparison -----------------------------------------------


class EmpireScreen(ModalScreen):
    """Scoreboard: tech count, planets, fleets, research velocity."""

    DEFAULT_CSS = """
    EmpireScreen {
        align: center middle;
    }
    EmpireScreen > Static {
        width: 80;
        height: auto;
        border: round #8a9cd2;
        background: #0b0f20;
        padding: 1 2;
    }
    """
    BINDINGS = [Binding("escape,q,e", "dismiss", "Close")]

    def __init__(self, game: Game) -> None:
        super().__init__()
        self.game = game

    def compose(self) -> ComposeResult:
        yield Static(self._render_body())

    def _render_body(self) -> Text:
        t = Text()
        t.append("EMPIRE SCOREBOARD", style="bold cyan")
        t.append(f"   Turn {self.game.turn}\n\n", style="dim")
        empires = sorted(
            self.game.empires,
            key=lambda e: (
                -len(self.game.planets_of(e.id)),
                -len(e.researched),
            ),
        )
        # Column headers.
        t.append(f"{'Rank':<5}{'Empire':<24}{'Planets':>8}{'Fleets':>8}"
                 f"{'Tech':>6}{'Score':>8}\n", style="bold")
        t.append("─" * 66 + "\n", style="dim")
        total_planets = max(sum(len(self.game.planets_of(e.id))
                                for e in self.game.empires), 1)
        for i, e in enumerate(empires):
            planets = len(self.game.planets_of(e.id))
            fleets = len(self.game.fleets_of(e.id))
            tech = len(e.researched)
            # Score = weighted composite.
            score = planets * 10 + tech * 3 + fleets * 2
            r, g, b = e.color
            style = f"rgb({r},{g},{b})"
            if e.is_player:
                style = "bold " + style
            badge = "★" if e.is_player else " "
            t.append(
                f" {badge} {i+1:<2}{e.name[:22]:<24}{planets:>8}{fleets:>8}"
                f"{tech:>6}{score:>8}\n",
                style=style,
            )
            # Planet share bar under each row.
            bar = _bar(planets, total_planets, 40)
            t.append(f"      {bar}\n", style=style)
        return t


# ---------- full-screen tech tree -------------------------------------------


class TechTreeScreen(ModalScreen):
    """Category-by-category tech tree with per-tech prereq list."""

    DEFAULT_CSS = """
    TechTreeScreen {
        align: center middle;
    }
    TechTreeScreen > Static {
        width: 110;
        height: 38;
        border: round #8a9cd2;
        background: #0b0f20;
        padding: 1 2;
        overflow-y: scroll;
    }
    """
    BINDINGS = [Binding("escape,q,T", "dismiss", "Close")]

    def __init__(self, game: Game) -> None:
        super().__init__()
        self.game = game

    def compose(self) -> ComposeResult:
        yield Static(self._render_body())

    def _render_body(self) -> Text:
        p = self.game.player()
        t = Text()
        t.append("TECH TREE", style="bold cyan")
        t.append(f"   {len(p.researched)}/{len(content.TECHS)} researched\n\n",
                 style="dim")
        by_cat = content.techs_by_category()
        order = ["LEARNING_CATEGORY", "GROWTH_CATEGORY", "PRODUCTION_CATEGORY",
                 "CONSTRUCTION_CATEGORY", "DEFENSE_CATEGORY",
                 "SHIP_HULLS_CATEGORY", "SHIP_WEAPONS_CATEGORY",
                 "SHIP_PARTS_CATEGORY", "SPY_CATEGORY"]
        ordered = [c for c in order if c in by_cat] + [
            c for c in by_cat if c not in order
        ]
        for cat_key in ordered:
            cat = content.CATEGORIES.get(cat_key)
            techs = by_cat[cat_key]
            done = sum(1 for tt in techs if tt.name in p.researched)
            t.append(
                f"{cat.short_name if cat else cat_key}  ",
                style=(
                    f"bold rgb({cat.color[0]},{cat.color[1]},{cat.color[2]})"
                    if cat else "bold"
                ),
            )
            t.append(f"{done}/{len(techs)}\n", style="dim")
            for tech in techs:
                if tech.name in p.researched:
                    marker, mstyle = "✓", "green"
                elif p.in_progress and p.in_progress.tech_name == tech.name:
                    marker, mstyle = "►", "bold cyan"
                elif tech.name in p.research_queue:
                    marker, mstyle = "…", "yellow"
                elif all(pr in p.researched for pr in tech.prerequisites):
                    marker, mstyle = "·", ""
                else:
                    marker, mstyle = "✗", "dim"
                prereq_str = ""
                if tech.prerequisites:
                    missing = [
                        pr for pr in tech.prerequisites
                        if pr not in p.researched
                    ]
                    if missing:
                        short = [content.TECHS[m].short_name
                                 for m in missing if m in content.TECHS]
                        prereq_str = f"  needs: {', '.join(short[:2])}"
                        if len(short) > 2:
                            prereq_str += f" +{len(short) - 2}"
                name = tech.short_name
                if len(name) > 28:
                    name = name[:27] + "…"
                t.append(f"  {marker} {name:<30} {tech.cost:>3} RP "
                         f"({tech.turns}t){prereq_str}\n", style=mstyle)
            t.append("\n")
        return t


# ---------- research queue editor -------------------------------------------


class ResearchQueueScreen(ModalScreen):
    """Shows current queue. j/k move cursor, d removes entry."""

    DEFAULT_CSS = """
    ResearchQueueScreen {
        align: center middle;
    }
    ResearchQueueScreen > Static {
        width: 70;
        height: auto;
        border: round #8a9cd2;
        background: #0b0f20;
        padding: 1 2;
    }
    """
    BINDINGS = [
        Binding("escape,q,R", "dismiss", "Close"),
        Binding("j", "move(1)", "Down"),
        Binding("k", "move(-1)", "Up"),
        Binding("d", "remove", "Delete"),
    ]

    def __init__(self, game: Game) -> None:
        super().__init__()
        self.game = game
        self.cursor_idx = 0

    def compose(self) -> ComposeResult:
        yield Static(self._render_body(), id="rq-body")

    def _render_body(self) -> Text:
        p = self.game.player()
        t = Text()
        t.append("RESEARCH QUEUE", style="bold cyan")
        t.append(f"   {p.rp_pool:.1f} RP pool\n\n", style="dim")
        if p.in_progress:
            tech = content.TECHS.get(p.in_progress.tech_name)
            if tech is not None:
                pct = int(100 * p.in_progress.points / max(tech.cost, 1))
                t.append(f"IN PROGRESS  ► {tech.short_name}  "
                         f"{p.in_progress.points:.0f}/{tech.cost} RP  "
                         f"({pct}%)\n\n", style="bold cyan")
        if not p.research_queue:
            t.append("  (queue empty — hit T to open the tech tree, "
                     "or t from map mode)\n", style="dim")
        for i, name in enumerate(p.research_queue):
            tech = content.TECHS.get(name)
            short = tech.short_name if tech else name
            cat = content.CATEGORIES.get(tech.category) if tech else None
            cat_style = (
                f"rgb({cat.color[0]},{cat.color[1]},{cat.color[2]})"
                if cat else ""
            )
            cursor = "▶" if i == self.cursor_idx else " "
            line = f"{cursor} {i+1:>2}. {short:<28} {tech.cost if tech else '?':>3} RP\n"
            t.append(line, style=("bold reverse " + cat_style).strip()
                     if i == self.cursor_idx else cat_style)
        t.append("\n[dim]j/k to move · d to delete · escape to close[/dim]\n")
        return t

    def _refresh(self) -> None:
        body = self.query_one("#rq-body", Static)
        body.update(self._render_body())

    def action_move(self, delta: int) -> None:
        p = self.game.player()
        if not p.research_queue:
            return
        self.cursor_idx = max(
            0, min(len(p.research_queue) - 1, self.cursor_idx + delta)
        )
        self._refresh()

    def action_remove(self) -> None:
        p = self.game.player()
        if not p.research_queue:
            return
        if self.game.dequeue_research(p.id, self.cursor_idx):
            self.cursor_idx = min(self.cursor_idx, len(p.research_queue) - 1)
            self.cursor_idx = max(0, self.cursor_idx)
            self._refresh()


# ---------- save / load ------------------------------------------------------


def save_game(game: Game, name: str) -> Path:
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    safe = "".join(c for c in name if c.isalnum() or c in "-_") or "save"
    path = SAVE_DIR / f"{safe}.fo"
    with open(path, "wb") as f:
        pickle.dump(game, f, protocol=pickle.HIGHEST_PROTOCOL)
    return path


def load_game(name: str) -> Optional[Game]:
    path = SAVE_DIR / (name if name.endswith(".fo") else f"{name}.fo")
    if not path.exists():
        return None
    try:
        with open(path, "rb") as f:
            data = pickle.load(f)
        if isinstance(data, Game):
            return data
    except Exception:
        return None
    return None


def list_saves() -> list[Path]:
    if not SAVE_DIR.exists():
        return []
    return sorted(SAVE_DIR.glob("*.fo"), key=lambda p: p.stat().st_mtime,
                  reverse=True)


class SaveScreen(ModalScreen):
    DEFAULT_CSS = """
    SaveScreen {
        align: center middle;
    }
    SaveScreen > Vertical {
        width: 60;
        height: 12;
        border: round #8a9cd2;
        background: #0b0f20;
        padding: 1 2;
    }
    SaveScreen Input {
        width: 100%;
    }
    """
    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
    ]

    def __init__(self, game: Game) -> None:
        super().__init__()
        self.game = game

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                Text.from_markup(
                    f"[bold cyan]SAVE GAME[/]   Turn {self.game.turn}\n\n"
                    f"Name (enter to save · escape to cancel):"
                ),
                id="save-title",
            )
            yield Input(placeholder="e.g. turn12", id="save-name")
            yield Static("", id="save-msg")

    def on_mount(self) -> None:
        self.query_one("#save-name", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        name = event.value.strip() or f"turn{self.game.turn}"
        try:
            path = save_game(self.game, name)
            self.query_one("#save-msg", Static).update(
                Text.from_markup(f"[green]✓ saved to {path.name} "
                                 f"({path.stat().st_size} bytes)[/]")
            )
            # Dismiss a moment later so user sees the confirmation.
            self.set_timer(0.6, self.dismiss)
        except Exception as exc:  # noqa: BLE001
            self.query_one("#save-msg", Static).update(
                Text.from_markup(f"[red]✗ {type(exc).__name__}: {exc}[/]")
            )


class LoadScreen(ModalScreen):
    DEFAULT_CSS = """
    LoadScreen {
        align: center middle;
    }
    LoadScreen > Vertical {
        width: 70;
        height: 20;
        border: round #8a9cd2;
        background: #0b0f20;
        padding: 1 2;
    }
    """
    BINDINGS = [
        Binding("escape,q", "dismiss", "Close"),
        Binding("j", "move(1)", "Down"),
        Binding("k", "move(-1)", "Up"),
        Binding("enter", "load", "Load"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.cursor_idx = 0
        self.saves = list_saves()
        self.loaded: Optional[Game] = None

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self._render_body(), id="load-body")

    def _render_body(self) -> Text:
        t = Text()
        t.append("LOAD GAME\n\n", style="bold cyan")
        if not self.saves:
            t.append("(no saves yet — use [bold]S[/] to save from map mode)\n",
                     style="dim")
            return t
        for i, path in enumerate(self.saves):
            cursor = "▶" if i == self.cursor_idx else " "
            size = path.stat().st_size
            ago = path.stat().st_mtime
            import time
            rel = time.strftime("%Y-%m-%d %H:%M",
                                time.localtime(ago))
            line = f"{cursor} {path.stem:<30} {size:>8} B   {rel}\n"
            if i == self.cursor_idx:
                t.append(line, style="bold reverse")
            else:
                t.append(line)
        t.append("\n[dim]j/k to move · enter loads · escape closes[/dim]\n")
        return t

    def _refresh(self) -> None:
        self.query_one("#load-body", Static).update(self._render_body())

    def action_move(self, delta: int) -> None:
        if not self.saves:
            return
        self.cursor_idx = max(0, min(len(self.saves) - 1,
                                     self.cursor_idx + delta))
        self._refresh()

    def action_load(self) -> None:
        if not self.saves:
            return
        path = self.saves[self.cursor_idx]
        game = load_game(path.stem)
        if game is None:
            self.query_one("#load-body", Static).update(
                Text.from_markup("[red]✗ failed to load (corrupt save?)[/]")
            )
            return
        self.loaded = game
        self.dismiss(game)
