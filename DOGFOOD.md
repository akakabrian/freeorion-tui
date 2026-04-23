# DOGFOOD — freeorion

_Session: 2026-04-23T13:11:06, driver: pty, duration: 0.5 min_

**PASS** — ran for 0.5m, captured 6 snap(s), 1 milestone(s), 0 blocker(s), 0 major(s).

## Summary

Ran a rule-based exploratory session via `pty` driver. Found no findings worth flagging. Game reached 22 unique state snapshots. Captured 1 milestone shot(s); top candidates promoted to `screenshots/candidates/`.

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
- Keys pressed: 330 (unique: 49)
- State samples: 43 (unique: 22)
- Score samples: 0
- Milestones captured: 1
- Phase durations (s): A=13.6, B=15.2, C=3.0
- Snapshots: `/home/brian/AI/projects/tui-dogfood/reports/snaps/freeorion-20260423-131032`

Unique keys exercised: -, /, 2, 3, 5, :, ;, ?, E, G, H, R, T, ], backspace, c, ctrl+l, delete, down, enter, escape, f, f1, f2, g, h, home, j, k, l, left, m, n, o, p, page_down, q, question_mark, r, right ...

## Milestones

| Event | t (s) | Interest | File | Note |
|---|---|---|---|---|
| first_input | 0.3 | 0.0 | `freeorion-20260423-131032/milestones/first_input.txt` | key=up |
