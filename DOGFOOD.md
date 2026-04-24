# DOGFOOD — freeorion

_Session: 2026-04-23T14:38:13, driver: pty, duration: 1.5 min_

**PASS** — ran for 1.2m, captured 8 snap(s), 1 milestone(s), 0 blocker(s), 0 major(s).

## Summary

Ran a rule-based exploratory session via `pty` driver. Found no findings worth flagging. Game reached 39 unique state snapshots. Captured 1 milestone shot(s); top candidates promoted to `screenshots/candidates/`.

## Findings

### Blockers

_None._

### Majors

_None._

### Minors

_None._

### Nits

_None._

### UX (feel-better-ifs)

_None._

## Coverage

- Driver backend: `pty`
- Keys pressed: 671 (unique: 66)
- State samples: 76 (unique: 39)
- Score samples: 0
- Milestones captured: 1
- Phase durations (s): A=42.7, B=20.9, C=9.0
- Snapshots: `/home/brian/AI/projects/tui-dogfood/reports/snaps/freeorion-20260423-143659`

Unique keys exercised: +, ,, -, ., /, 0, 1, 2, 3, 4, 5, :, ;, =, ?, E, G, H, R, T, [, ], a, b, backspace, c, ctrl+l, d, delete, down, end, enter, escape, f, f1, f2, g, h, home, j ...

## Milestones

| Event | t (s) | Interest | File | Note |
|---|---|---|---|---|
| first_input | 0.3 | 0.0 | `freeorion-20260423-143659/milestones/first_input.txt` | key=up |
