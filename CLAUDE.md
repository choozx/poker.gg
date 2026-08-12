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

Modules, strict dependency direction `convert ← store ← {bankroll, quiz} ← gui`:

- **`convert.py`** — the parser. Regex-based, line-by-line. `parse_hand(text)` → `Hand` dataclass;
  `split_hands(text)` splits a file on `CoinPoker Hand #`. Also renders markdown (`render_markdown`,
  the AI-analysis format) and JSON, and is a standalone CLI. No state, no I/O beyond the CLI.
- **`store.py`** — the DB layer over `hands_db.json`. Load/save/merge plus all aggregate queries
  (`stats`, `hand_grid`, `tournament_list`, `review_hands`). Imports from `convert` only.
- **`gui.py`** — the HTTP server (`http.server`, threaded) **and the entire frontend**, which lives
  as one big `INDEX_HTML` string (HTML+CSS+vanilla JS). Also holds the AI backends and prompts.
- **`bankroll.py`** — the **real-money** domain (kept strictly separate from chip EV; see below).
- **`quiz.py`** — the 🎯 문제 풀기 domain: leak-spot detection + question picking (see below).

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

### 🎯 문제 풀기 (`quiz.py`) — 리크 스팟에서 출제, AI는 채점만

Sidebar `SEL = -7`. The design rule is **출제는 로컬(공짜), 채점만 AI**: a real hand from the DB is
cut at a hero decision point and served as a multiple-choice question; the AI only grades the
answer. The correct answer is deliberately **not** "what hero actually did" — the hand was selected
*because* that action was suspect. `quiz.reveal()` discloses the real action and result only after
grading, via a separate endpoint, so nothing leaks early.

The cut is `convert.render_markdown(hand, hero, stop_at=<Action>)`, which stops before that action
and drops later streets, `SHOWDOWN` and `RESULT`. **Any change there risks leaking the outcome into
the question** — that is the one thing this feature must never do.

Two leak axes (`quiz.leak_spots`), deliberately kept separate because they degrade differently:

- **휴리스틱** (`_heuristic_spots`) — groups the frozen `review` field (큰 손실 / 쇼다운 패배 /
  올인 패배) by position (× stack bucket). Works on **old, un-rebuilt DBs**.
- **통계 이탈** (`_freq_spots`) — position × stack-bucket RFI% and vs-raise continue% versus
  `RFI_BASE`/`VS_RAISE_BASE`. Needs `pf_faced`/`stack_bb`, so it **auto-disables** on un-rebuilt DBs
  (`freq_available()`), and the UI shows a `--rebuild` hint. Both states must keep working.

The baselines are rough MTT reference values, *not* solver output — they only choose which spot to
ask about; the AI does the judging, so a slightly-off baseline just means a fine spot gets asked and
graded `[좋음]`. The two axes' scores are in different units, so `leak_spots` **interleaves** them
by rank rather than sorting on a shared score (otherwise 통계 이탈 takes every top slot), and
`next_question` weights by that interleaved rank.

Three multi-select toggle rows scope what gets asked: `?pos=BB,SB&stack=pf,deep&street=turn,river`
(empty = all). 포지션/스택 filter **spots** — a spot is keyed by `(포지션, 스택버킷, 사유)` — and
position matching goes through `_norm_pos` so the one `MP` toggle covers MP1/MP2/MP3.
`filter_options()` counts come from the **unfiltered** spot list, so toggles show what exists rather
than reacting to the current selection.

**스트릿 is not a spot attribute** — it belongs to the decision point inside a hand, so it can only
be applied after parsing. Two places handle it: `leak_spots` drops 통계 이탈 spots when 프리플랍
isn't selected (RFI/defend frequency are preflop metrics — a turn question must not be labelled
"오픈 과다"), and `_pick_decision(spot, decisions, streets)` picks a decision on an allowed street,
returning `None` if the hand never got there.

Because of that, a spot can pass the pos/stack filter yet yield no hand on the wanted street
(`<15bb · 올인 패배` never reaches a river decision). `next_question` therefore tries up to
`MAX_SPOT_TRIES` spots × `SCAN_PER_SPOT` hands before falling back to AI generation — with one spot
only, ~36% of river requests fell back needlessly. Keep that retry if you touch this: the fallback
costs an AI call per question.

`/api/quiz/next?spot=<key>` still pins one exact spot (looked up in the **unfiltered** list) but is
no longer surfaced in the UI — the spot-chip row was removed as unused.

When a spot's unserved real hands drop below 3, `next_question` returns `{"generate": spot}` and
`POST /api/quiz/gen` has the AI invent a same-shape practice hand (`QUIZ_GEN_SYSTEM_PROMPT`, returns
JSON parsed by `_quiz_parse_gen`). Generated questions are ephemeral — never written to the DB.

`db["quiz"]` holds `attempts` (capped 500) and `cache` (capped 400) — the cache keys on
`hand_id:didx:choice_id`, so re-answering an identical question costs **zero AI calls**
(`X-AI-Backend: cache`). Keep both caps: the DB is cloud-synced.

API: `GET /api/quiz/spots` · `/api/quiz/next?spot=` · `/api/quiz/reveal?hand_id=&didx=` ·
`POST /api/quiz/grade` (streams) · `/api/quiz/gen`.

### ⏱ 토너먼트 타이머 — frontend-only, no server state

A live blind clock (sidebar `SEL = -6`), entirely inside `INDEX_HTML`'s JS (`tm*` functions,
`TIMER` state). It touches **no Python, no endpoint, no `hands_db.json`** — settings and run state
persist to `localStorage` under `ahh_timer_v1` only, so it is not cloud-synced. Keep it that way
unless the user asks for cross-device timers.

`tmSchedule()` flattens levels + breaks into one segment array (`at` = ms offset from start);
blinds come from a multiplier ladder (`TM_LADDERS` slow/med/fast) applied to the starting SB and
snapped to poker-friendly numbers by `tmNiceSb` (level 1 keeps the user's exact SB). Presets
(`TM_PRESETS`: hyper/turbo/classic/deep) just bulk-set the config; editing any structure field
(`TM_STRUCT_KEYS`) flips `preset` to `custom`. Elapsed time is `base + (now - startedAt)` — a
wall-clock model, so closing the browser mid-tournament and returning advances the clock, which is
intentional. The 250ms tick runs whenever the timer is playing (even on other tabs) so the level-up
and 1-minute-warning beeps still fire; it only writes to the DOM when `SEL === -6`.

Money side is pure arithmetic on the four user inputs (buy-in, prize %, ITM %, entrants):
`pool = buyin × entrants × prizeRate%`, `itm = ceil(entrants × itmRate%)`, bubble = `remaining − itm`.
No payout ladder is modeled — don't invent one.

### The key invariant: metadata is frozen at import time

When a hand is imported, `convert.hand_meta()` computes derived fields (`vpip`, `pfr`, `rfi`,
`rfi_opp`, `pf_action`, `pf_faced`, `stack_bb`, `net_bb`, `review`, `hero_pos`, …) **once** and stores them in
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
- **`pf_faced`** records what hero *faced* preflop: `none` (folded to hero) / `limp` / `raise` /
  `None` (no preflop decision at all, e.g. a BB walk). Don't try to re-derive this from
  `rfi_opp` + `pf_action` + `no_action_fold` — those can't separate "faced a raise" from "faced a
  limp" from "walk", and `no_action_fold` is `True` for exactly the clean preflop fold, so filtering
  it out silently drops every fold from a defend-frequency denominator.
- **Chip EV (`net_bb`)** is a play-quality metric, not winnings — tournament chips ≠ prize money, so
  the app never sums P&L as money.
- Hand-grid stack buckets: `<15` (push/fold) / `15–25` / `25–40` / `40+` bb.
