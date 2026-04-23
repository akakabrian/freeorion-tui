"""Load real FreeOrion content from the vendored repo.

FreeOrion's `.focs.py` files are lightweight Python declarations with a
consistent `Tech(name=..., category=..., researchcost=..., researchturns=...,
prerequisites=[...])` shape. We parse them with regex (no exec) so we
don't need the `focs` Python package installed.

If the vendor tree is missing, we fall back to a small built-in tech list
so the game still runs. The built-in set is deliberately short — the
point of vendoring FreeOrion is to get its 194-tech tree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VENDOR_SCRIPTING = REPO / "engine" / "freeorion" / "default" / "scripting"
TECHS_DIR = VENDOR_SCRIPTING / "techs"
CATEGORIES_FILE = TECHS_DIR / "Categories.inf.py"


@dataclass
class Tech:
    name: str
    category: str
    cost: int  # research points
    turns: int  # minimum turns even with unlimited RP
    prerequisites: list[str] = field(default_factory=list)
    description: str = ""

    @property
    def short_name(self) -> str:
        """`LRN_ALGO_ELEGANCE` → `Algo Elegance` for display."""
        stem = self.name.split("_", 1)[-1] if "_" in self.name else self.name
        return stem.replace("_", " ").title()


@dataclass
class Category:
    name: str
    color: tuple[int, int, int]  # RGB

    @property
    def short_name(self) -> str:
        return self.name.replace("_CATEGORY", "").replace("_", " ").title()


# --- regex parsers --------------------------------------------------

_TECH_NAME = re.compile(r'name\s*=\s*"([^"]+)"')
_TECH_CATEGORY = re.compile(r'category\s*=\s*"([^"]+)"')
# researchcost may be `18 * TECH_COST_MULTIPLIER` — we capture the literal
# number and let the display show it as-is. Multiplier is ~1.0 in practice.
_TECH_COST = re.compile(r"researchcost\s*=\s*(\d+(?:\.\d+)?)")
_TECH_TURNS = re.compile(r"researchturns\s*=\s*(\d+)")
_TECH_PREREQ_BLOCK = re.compile(r"prerequisites\s*=\s*\[([^\]]*)\]", re.DOTALL)
_TECH_PREREQ_ITEM = re.compile(r'"([^"]+)"')

_CAT_NAME = re.compile(r'name\s*=\s*"([^"]+)"')
_CAT_COLOUR = re.compile(r"colour\s*=\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)")


def _parse_tech_file(path: Path) -> Tech | None:
    text = path.read_text()
    m_name = _TECH_NAME.search(text)
    m_cat = _TECH_CATEGORY.search(text)
    m_cost = _TECH_COST.search(text)
    m_turns = _TECH_TURNS.search(text)
    if not (m_name and m_cat):
        return None
    prereqs: list[str] = []
    m_prereq = _TECH_PREREQ_BLOCK.search(text)
    if m_prereq:
        prereqs = _TECH_PREREQ_ITEM.findall(m_prereq.group(1))
    cost = int(float(m_cost.group(1))) if m_cost else 20
    turns = int(m_turns.group(1)) if m_turns else 3
    return Tech(
        name=m_name.group(1),
        category=m_cat.group(1),
        cost=cost,
        turns=turns,
        prerequisites=prereqs,
    )


def _parse_categories(path: Path) -> dict[str, Category]:
    """Categories.inf.py has multiple Category(name=..., colour=...) calls."""
    text = path.read_text()
    cats: dict[str, Category] = {}
    # Split on `Category(` occurrences — each block gets its own regex sweep.
    for block in re.split(r"\bCategory\s*\(", text)[1:]:
        m_name = _CAT_NAME.search(block)
        m_col = _CAT_COLOUR.search(block)
        if not m_name:
            continue
        rgb = (255, 255, 255)
        if m_col:
            rgb = (int(m_col.group(1)), int(m_col.group(2)), int(m_col.group(3)))
        cats[m_name.group(1)] = Category(name=m_name.group(1), color=rgb)
    return cats


# --- module-level load ---------------------------------------------

def _builtin_fallback() -> tuple[dict[str, Tech], dict[str, Category]]:
    """Small hand-authored tree used when vendor repo is absent."""
    cats = {
        "LEARNING_CATEGORY": Category("LEARNING_CATEGORY", (54, 202, 229)),
        "GROWTH_CATEGORY": Category("GROWTH_CATEGORY", (116, 225, 107)),
        "PRODUCTION_CATEGORY": Category("PRODUCTION_CATEGORY", (240, 106, 106)),
        "DEFENSE_CATEGORY": Category("DEFENSE_CATEGORY", (70, 80, 215)),
    }
    techs = {
        "LRN_ALGO_ELEGANCE": Tech("LRN_ALGO_ELEGANCE", "LEARNING_CATEGORY", 18, 3),
        "LRN_LEARNING": Tech("LRN_LEARNING", "LEARNING_CATEGORY", 15, 2),
        "LRN_PHASING": Tech("LRN_PHASING", "LEARNING_CATEGORY", 60, 5,
                            ["LRN_LEARNING"]),
        "GRO_SUBTER_FARMING": Tech("GRO_SUBTER_FARMING", "GROWTH_CATEGORY", 12, 2),
        "GRO_PLANET_ECOL": Tech("GRO_PLANET_ECOL", "GROWTH_CATEGORY", 20, 3,
                                ["GRO_SUBTER_FARMING"]),
        "PRO_ROBOTIC_PROD": Tech("PRO_ROBOTIC_PROD", "PRODUCTION_CATEGORY", 20, 3),
        "PRO_INDUSTRY_CENTRE_I": Tech("PRO_INDUSTRY_CENTRE_I", "PRODUCTION_CATEGORY",
                                      50, 4, ["PRO_ROBOTIC_PROD"]),
        "DEF_DEFENSE_NET": Tech("DEF_DEFENSE_NET", "DEFENSE_CATEGORY", 15, 3),
        "DEF_GARRISON_1": Tech("DEF_GARRISON_1", "DEFENSE_CATEGORY", 20, 3,
                               ["DEF_DEFENSE_NET"]),
    }
    return techs, cats


def _load() -> tuple[dict[str, Tech], dict[str, Category]]:
    if not TECHS_DIR.exists():
        return _builtin_fallback()
    techs: dict[str, Tech] = {}
    for path in TECHS_DIR.rglob("*.focs.py"):
        if path.name == "Categories.inf.py":
            continue
        try:
            t = _parse_tech_file(path)
            if t is not None:
                techs[t.name] = t
        except Exception:
            # Skip any file that fails to parse — some .focs.py files
            # include dynamic content we don't support.
            continue
    cats: dict[str, Category] = {}
    if CATEGORIES_FILE.exists():
        try:
            cats = _parse_categories(CATEGORIES_FILE)
        except Exception:
            pass
    if not techs or not cats:
        return _builtin_fallback()
    # Drop prerequisites that point to missing techs — avoids soft locks
    # when some tech files failed to parse.
    for t in techs.values():
        t.prerequisites = [p for p in t.prerequisites if p in techs]
    return techs, cats


TECHS, CATEGORIES = _load()


# --- star name extraction ------------------------------------------

def _load_star_names() -> list[str]:
    """Pull the first few hundred real star names from FreeOrion.

    The `starnames.py` module is heavy (uses `import freeorion as fo`), but
    `default/stringtables/en.txt` has a flat list of star name translations
    we can grep. Fallback is a compact curated list of real stars so the
    game still generates authentic-looking maps without vendor."""
    fallback = [
        "Sol", "Alpha Centauri", "Sirius", "Vega", "Arcturus", "Betelgeuse",
        "Rigel", "Procyon", "Altair", "Deneb", "Antares", "Aldebaran",
        "Pollux", "Capella", "Spica", "Fomalhaut", "Regulus", "Canopus",
        "Achernar", "Bellatrix", "Castor", "Mira", "Polaris", "Mintaka",
        "Alnilam", "Alnitak", "Saiph", "Nunki", "Kaus", "Shaula", "Sabik",
        "Gienah", "Algol", "Adhara", "Peacock", "Markab", "Scheat", "Alpheratz",
        "Diphda", "Hamal", "Mirach", "Almach", "Enif", "Sadr", "Mirfak",
        "Wezen", "Avior", "Atria", "Alkaid", "Menkar", "Alderamin", "Elnath",
        "Tarazed", "Rasalhague", "Izar", "Cor Caroli", "Vindemiatrix",
        "Mizar", "Alcor", "Thuban", "Kochab", "Ruchbah", "Caph", "Segin",
        "Gomeisa", "Furud", "Aludra", "Alphard", "Alkes", "Zubeneschamali",
        "Agena", "Kraz", "Algedi", "Dabih", "Nashira", "Deneb Algedi",
        "Sadalmelik", "Sadalsuud", "Skat", "Ancha", "Biham", "Homam",
        "Matar", "Baham", "Sadalbari", "Errai", "Alrakis", "Kitalpha",
        "Altais", "Rastaban", "Eltanin", "Aldhibah", "Tyl", "Merak",
        "Dubhe", "Megrez", "Phecda", "Alioth", "Talitha", "Tania",
        "Chara", "Alula", "Asterion", "Acamar", "Zaurak", "Sceptrum",
        "Keid", "Beid", "Ran", "Cursa", "Phact", "Wazn", "Mintaka",
        "Nihal", "Arneb", "Saidak", "Furud", "Tureis", "Naos", "Azmidi",
        "Markeb", "Tian Guan", "Tien Kuan", "Propus", "Tejat", "Alhena",
        "Wasat", "Mebsuta", "Mekbuda", "Alzirr", "Alula Borealis",
    ]
    en_path = REPO / "engine" / "freeorion" / "default" / "stringtables" / "en.txt"
    if not en_path.exists():
        return fallback
    # Heuristic: star names in the stringtable appear as single-word
    # entries in all-caps under a STAR_NAMES section. Without that
    # tagging, fall back.
    try:
        text = en_path.read_text(errors="ignore")
        # Look for a STAR_NAMES block — this is a rough grep since the
        # format is key/value pairs separated by blank lines.
        if "STAR_NAMES" in text:
            block_idx = text.find("STAR_NAMES")
            block = text[block_idx:block_idx + 8000]
            # Names are on their own lines, simple words.
            candidates = re.findall(r"^([A-Z][a-zA-Z' -]{2,20})$", block, re.MULTILINE)
            if len(candidates) >= 30:
                return candidates[:300]
    except Exception:
        pass
    return fallback


STAR_NAMES = _load_star_names()


# --- helpers --------------------------------------------------------

def techs_by_category() -> dict[str, list[Tech]]:
    """Group techs by category, sorted by name within each."""
    out: dict[str, list[Tech]] = {}
    for t in TECHS.values():
        out.setdefault(t.category, []).append(t)
    for lst in out.values():
        lst.sort(key=lambda t: t.name)
    return out
