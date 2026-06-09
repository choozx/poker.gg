# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A local web app that converts CoinPoker tournament hand-history `.txt` files into a readable format
and runs per-hand AI (Claude) analysis. Pure Python **standard library only** — no external packages,
no build step, no test suite. Target: Python 3.8+. UI text and code comments are in Korean.

## Commands

```bash
python3 gui.py                      # start web app at http://127.0.0.1:8765 (auto-opens browser)
python3 gui.py --port 9000          # change port (use on "Address already in use")
python3 gui.py --no-browser
python3 gui.py --ai cli|api|auto    # pick AI backend (default: auto)
python3 gui.py --rebuild            # re-derive ALL hand metadata from stored raw text (see below)
python3 gui.py --hero <name>        # hero player name (default: "Hero")

python3 convert.py hands.txt              # interactive: list tournaments → convert to markdown
python3 convert.py hands.txt --list       # list tournaments only
python3 convert.py hands.txt --tournament 63446 -o out.md
python3 convert.py hands.txt --format json
```

There are no tests, linters, or CI. Verify changes by running `gui.py` against `sample_hand.txt`
(drag-drop into the browser) or `python3 convert.py sample_hand.txt`.

## Architecture

Three modules, strict dependency direction `convert ← store ← gui`:

- **`convert.py`** — the parser. Regex-based, line-by-line. `parse_hand(text)` → `Hand` dataclass;
  `split_hands(text)` splits a file on `CoinPoker Hand #`. Also renders markdown (`render_markdown`,
  the AI-analysis format) and JSON, and is a standalone CLI. No state, no I/O beyond the CLI.
- **`store.py`** — the DB layer over `hands_db.json`. Load/save/merge plus all aggregate queries
  (`stats`, `hand_grid`, `tournament_list`, `review_hands`). Imports from `convert` only.
- **`gui.py`** — the HTTP server (`http.server`, threaded) **and the entire frontend**, which lives
  as one big `INDEX_HTML` string (HTML+CSS+vanilla JS). Also holds the AI backends and prompts.
- **`bankroll.py`** — the **real-money** domain (kept strictly separate from chip EV; see below).

### Bankroll (real money) — a parallel domain to the hands

`bankroll.py` tracks actual tournament results (buy-in/cash/profit, in USD/₮) at
`db["bankroll"]["entries"]` — **deliberately separate from chip EV (`net_bb`)**, since the app's
core principle is that tournament chips ≠ money. It was seeded **once** by migrating the user's
Google Sheet (`migrate_from_sheet`, ID in `SHEET_ID`, read via stdlib `urllib`+`zipfile`); the app
is now the source of truth — **do not re-run migration**, it replaces `db["bankroll"]` wholesale.

Each entry is matched to a hand-history tournament (`tournament_id`) to join money ↔ play quality
(the 💰 뱅크롤 tab: summary, cumulative P&L, per-tournament drill-through). Matching is the subtle
part — sheet rows have no tournament ID, so they're paired by name+date via an order-preserving
alignment (`_align`, Needleman-Wunsch over dates) per `_match_key` group, with: chronological
ordering (the sheet is time-sorted), a `_session_date` shift (a hand dealt just after midnight
belongs to the previous day's tournament-start session), generic-`freeroll` grouping, satellite
detection (`is_satellite`/`is_ticket_entry` — a name's ₮ is the *destination*, not the buy-in), and
same-day deep-run preference (cashed rows resist being left unmatched). `set_override` force-links a
specific entry and survives migration. API: `GET /api/bankroll`, `POST /api/bankroll/entry` and
`/api/bankroll/delete`.

### The key invariant: metadata is frozen at import time

When a hand is imported, `convert.hand_meta()` computes derived fields (`vpip`, `pfr`, `rfi`,
`rfi_opp`, `pf_action`, `stack_bb`, `net_bb`, `review`, `hero_pos`, …) **once** and stores them in
the DB record alongside the original `raw` text and rendered `markdown`. The aggregate queries in
`store.py` (`stats`, `hand_grid`) read these frozen fields directly — they never re-parse `raw`.

Consequence: **if you change parsing or any derived field in `hand_meta()`, existing DB records keep
their old values.** The new field will be missing/empty for already-imported hands until the user runs
`python3 gui.py --rebuild`, which re-runs `build_record` over every stored `raw` (preserving AI
`analysis`). The UI deliberately shows `—` / "run `--rebuild`" placeholders when a field is absent
(old DBs predate `pfr`/`rfi`/`pf_action`/`stack_bb`). When adding a metadata field, account for both
the rebuilt and not-yet-rebuilt states.

### Data model & DB

`hands_db.json` is `{"version", "hands": {<hand_id>: record}, "report", "updated_at"}`, keyed by
hand number. Re-importing is idempotent — `import_text` skips hand IDs already present, so overlapping
date ranges are safe. Saves are atomic (write `.tmp` → `os.replace`) under a lock. The DB is **not in
git** (`.gitignore`); it is the single source of truth (holds raw text, so it's portable and
rebuildable). It is large (~90MB) — don't read it whole; query via `store.py` helpers.

### Performance shape

The frontend stays light by lazy-loading: `/api/db` returns only the tournament list (no hand
bodies); hands for one tournament load on click via `/api/tournament?id=`. `raw` and `markdown` are
stripped from list responses. Keep this split when adding endpoints.

### HTTP API (all in `gui.py`)

- `GET /api/db` · `/api/stats` · `/api/review` · `/api/tournament?id=` · `/api/handgrid?pos=&stack=`
- `POST /api/import?hero=` (raw txt body), `/api/analyze`, `/api/report`
- `/api/analyze` and `/api/report` stream AI text back chunk-by-chunk (`_stream_ai`); the completed
  text is persisted to the DB only on a clean finish (partial/aborted streams are discarded).

### AI backends

Pluggable: `AnthropicAPIBackend` (needs `pip install anthropic` + `ANTHROPIC_API_KEY`) and
`ClaudeCLIBackend` (headless `claude -p`, no key, uses the user's Claude subscription). `--ai auto`
prefers API if a key is present, else CLI. Both expose `available()` and `stream(system, user)`.
Prompts are `ANALYSIS_SYSTEM_PROMPT` (per-hand) and `REPORT_SYSTEM_PROMPT` (combined report), both
near the top of `gui.py`. The analysis prompt requires each street verdict and the overall verdict to
start with `[좋음/무난/의문/실수]` — the frontend parses that grade out for badge emojis, so keep the
format if you touch the prompt.

## Poker-domain notes

- **Positions** are assigned from the button seat in `convert.assign_positions` (heads-up: button = SB).
- **RFI** (`rfi`/`rfi_opp`) follows the solver "open" definition: `rfi_opp` = folded-to-hero (open
  opportunity), `rfi` = first-in raise. `pf_action` classifies hero's first voluntary preflop action
  (open / 3bet / call / allin / fold) for the hand-grid action stack-bars.
- **Chip EV (`net_bb`)** is a play-quality metric, not winnings — tournament chips ≠ prize money, so
  the app never sums P&L as money.
- Hand-grid stack buckets: `<15` (push/fold) / `15–25` / `25–40` / `40+` bb.
