"""Entry point — ``python freeorion.py [--seed N] [--size N] [--agent]``."""

from __future__ import annotations

import argparse

from freeorion_tui.app import run


def main() -> None:
    p = argparse.ArgumentParser(prog="freeorion-tui")
    p.add_argument("--seed", type=int, default=None,
                   help="galaxy generator seed (default: random)")
    p.add_argument("--size", type=int, default=40,
                   help="number of star systems (default: 40)")
    p.add_argument("--agent", action="store_true",
                   help="start the agent HTTP API alongside the TUI")
    p.add_argument("--agent-port", type=int, default=8789,
                   help="port for the agent API (default: 8789)")
    p.add_argument("--headless", action="store_true",
                   help="no TUI — just the sim + agent API")
    args = p.parse_args()
    port = args.agent_port if (args.agent or args.headless) else None
    run(seed=args.seed, galaxy_size=args.size,
        agent_port=port, headless=args.headless)


if __name__ == "__main__":
    main()
