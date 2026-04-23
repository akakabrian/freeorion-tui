"""End-to-end PTY playtest.

Drives the real TUI binary (``freeorion.py``) through a pseudo-terminal
with pexpect, exercising the key user flows: boot, cycle overlay, open
the tech-tree modal (T), open the research queue editor (R), end turn,
and quit. At each milestone we capture the raw screen buffer and dump it
as an SVG snapshot under ``tests/out/playtest_*.svg``.

This complements ``tests/qa.py`` (which pilots the Textual app in-process
via Pilot) by proving the entry-point script, PTY handling, and real key
bindings all still work.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

import pexpect

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "out"
OUT.mkdir(parents=True, exist_ok=True)


# SVG template. We wrap the captured terminal bytes in a <text> node so the
# output is a) tiny, b) grep-able, and c) viewable in any SVG viewer.
SVG_HEAD = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 640" '
    'font-family="DejaVu Sans Mono, monospace" font-size="11">'
    '<rect width="100%" height="100%" fill="#0b0f20"/>'
)
SVG_TAIL = "</svg>"


def _strip_ansi(data: bytes) -> str:
    """Strip ANSI escape sequences so the SVG stays readable."""
    import re
    text = data.decode("utf-8", errors="replace")
    # CSI sequences
    text = re.sub(r"\x1b\[[\d;?]*[ -/]*[@-~]", "", text)
    # OSC sequences (terminated by BEL or ST)
    text = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", text)
    # Lone ESCs + control chars we don't care about
    text = re.sub(r"\x1b[()][\x20-\x7e]", "", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    return text


def _drain(child: pexpect.spawn, window: float = 0.25) -> bytes:
    """Greedily read everything the app has emitted so far."""
    out = bytearray()
    end = time.time() + window
    while time.time() < end:
        try:
            chunk = child.read_nonblocking(size=8192, timeout=0.05)
            if chunk:
                out.extend(chunk if isinstance(chunk, (bytes, bytearray))
                           else chunk.encode("utf-8", errors="replace"))
                end = time.time() + window  # extend while still producing
        except pexpect.exceptions.TIMEOUT:
            break
        except pexpect.exceptions.EOF:
            break
    return bytes(out)


def _snapshot(child: pexpect.spawn, label: str, tag: str,
              buffer: bytes = b"") -> Path:
    """Write a compact SVG snapshot of the current screen."""
    raw = buffer or (child.before or b"")
    text = _strip_ansi(raw)
    # Keep the last ~48 lines so the image fits in the viewBox.
    lines = text.splitlines()[-48:]
    path = OUT / f"playtest_{tag}.svg"
    parts = [SVG_HEAD,
             f'<text x="10" y="16" fill="#8fc2ff" font-weight="bold">'
             f'{label}</text>']
    y = 34
    for line in lines:
        # xml-escape the few chars that matter
        safe = (line.replace("&", "&amp;")
                    .replace("<", "&lt;").replace(">", "&gt;"))
        parts.append(f'<text x="10" y="{y}" fill="#d8e0f0">'
                     f'{safe[:200]}</text>')
        y += 13
    parts.append(SVG_TAIL)
    path.write_text("\n".join(parts))
    return path


def run() -> int:
    env = os.environ.copy()
    env["TERM"] = "xterm-256color"
    env["COLUMNS"] = "140"
    env["LINES"] = "48"
    # Force colour and a deterministic seed.
    env["PYTHONUNBUFFERED"] = "1"

    cmd = sys.executable
    args = [str(ROOT / "freeorion.py"), "--seed", "42", "--size", "30"]
    print(f"[playtest] spawning {cmd} {' '.join(args)}")
    spawn: Any = pexpect.spawn  # pexpect's env kwarg is typed as os._Environ
    child = spawn(
        cmd, args, env=env, timeout=15,
        dimensions=(48, 140),
        codec_errors="replace",
        encoding=None,  # keep bytes for SVG dump
    )

    # Wait for the app's startup banner — we look for "Welcome" which the
    # log_msg call drops on mount.
    child.expect(b"Welcome", timeout=12)
    boot = _drain(child, window=0.6)
    _snapshot(child, "boot", "01_boot", buffer=boot)

    # Cycle overlay three times (none → owners → population → research).
    for _ in range(3):
        child.send("o")
        time.sleep(0.2)
    _snapshot(child, "overlay cycled x3", "02_overlay",
              buffer=_drain(child, 0.4))

    # Open full-screen tech tree modal.
    child.send("T")
    time.sleep(0.3)
    _snapshot(child, "tech tree modal", "03_techtree",
              buffer=_drain(child, 0.5))
    child.send("\x1b")  # escape
    time.sleep(0.2)
    _drain(child, 0.2)

    # Open research queue editor.
    child.send("R")
    time.sleep(0.3)
    _snapshot(child, "research queue editor", "04_research_queue",
              buffer=_drain(child, 0.4))
    child.send("\x1b")
    time.sleep(0.2)
    _drain(child, 0.2)

    # End turn (space) a few times.
    for _ in range(3):
        child.send(" ")
        time.sleep(0.2)
    _snapshot(child, "after 3 end-turns", "05_post_turn",
              buffer=_drain(child, 0.4))

    # Quit.
    child.send("q")
    try:
        child.expect(pexpect.EOF, timeout=5)
    except pexpect.TIMEOUT:
        child.terminate(force=True)

    exit_code = child.exitstatus if child.exitstatus is not None else -1
    print(f"[playtest] exited {exit_code}")
    print(f"[playtest] snapshots in {OUT}")
    for p in sorted(OUT.glob("playtest_*.svg")):
        print(f"  - {p.name} ({p.stat().st_size} B)")
    return 0 if exit_code in (0, -1) else exit_code


if __name__ == "__main__":
    sys.exit(run())
