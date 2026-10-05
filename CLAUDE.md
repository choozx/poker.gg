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

Modules, strict dependency direction `convert ← store ← {bankroll, quiz, ranges} ← gui`:

- **`convert.py`** — the parser. Regex-based, line-by-line. `parse_hand(text)` → `Hand` dataclass;
  `split_hands(text)` splits a file on `CoinPoker Hand #`. Also renders markdown (`render_markdown`,
  the AI-analysis format) and JSON, and is a standalone CLI. No state, no I/O beyond the CLI.
- **`store.py`** — the DB layer over `hands_db.json`. Load/save/merge plus all aggregate queries
  (`stats`, `hand_grid`, `tournament_list`, `review_hands`). Imports from `convert` only.
- **`gui.py`** — the HTTP server (`http.server`, threaded) **and the entire frontend**, which lives
  as one big `INDEX_HTML` string (HTML+CSS+vanilla JS). Also holds the AI backends and prompts.
- **`bankroll.py`** — the **real-money** domain (kept strictly separate from chip EV; see below).
- **`quiz.py`** — the 🎯 문제 풀기 domain: leak-spot detection + question picking (see below).
- **`ranges.py`** — the 📐 오픈 레인지 drill: preflop RFI charts + **local** grading (see below).
  A sibling of `quiz.py`; the two never import each other.

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

The 캠페인 트리 (`campaigns`) nests satellites under their main event by name/date inference. When that
guess is wrong, or for multi-day events (Day 1 flights → Day 2), an entry can carry a **manual**
`kind` (`single` 싱글데이 / `satellite` / `qualifier` Day1·플라이트) plus `parent_id`, set from the
edit form's 게임 타입 select. No `kind` = auto-detect (old behaviour); `kind: "auto"` on update
clears it. Manual children are pulled out of the inference and attached afterwards under the root
that their `parent_id` chain resolves to (the tree is 2 levels deep, so chains flatten; a cycle or a
deleted parent leaves a standalone row). The tree is display-only: money totals ignore it.

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

(The 📐 drill's own 포지션·스택 filter is a **dropdown** pair, not toggles — `qzSelectRow`, "전체 또는
하나". It still writes the same `RANGE.pos`/`RANGE.stack` arrays and the same `?pos=&stack=`
querystring, so the server side is unchanged; only 🃏 핸드 리뷰 below keeps multi-select toggles,
since there a spot list genuinely benefits from picking several.)

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

### 📐 오픈 레인지 드릴 (`ranges.py`) — 차트가 정답, AI 호출 0회

The second mode of the 🎯 문제 풀기 tab (`QUIZ.mode = 'hand' | 'range'`, same `SEL = -7`). Where
`quiz.py` is "출제 로컬, 채점 AI", this one is **local end to end** — the answer is in a chart, so
grading is deterministic and costs nothing. Don't route it through an AI backend.

`RFI[pos][bucket] = (open_notation, mix_notation)` holds 24 charts (UTG/MP/CO/BTN/SB × the four
`store._stack_bucket` keys, plus one shared `SB(BTN)` heads-up chart that every bucket falls back
to via `_BUCKET_FALLBACK`). The strings are expanded to 169-combo sets by `expand()` (`22+`,
`A2s+`, `A5s-A2s`, `K5o-K7o`) and cached; labels match `store._combo` exactly, so hand-grid data
and chart data join on the same keys. `mix` is the boundary band: **either answer grades [무난]**.
`mix -= open`, so an overlap in the notation is harmless.

The charts are **approximations of published MTT reference charts, not solver output** — this app
is stdlib-only and offline. That's why the `mix` band exists and why the drill never claims more
precision than it has. `python3 ranges.py` prints every chart's open%/mix% for a sanity check after
an edit; it also asserts every token expands to a real combo. Two properties worth preserving: no
chart should be all open+mix (nothing left to grade [실수] — the heads-up chart hit this once), and
open% should stay near the position's usual MTT RFI (UTG ~15 / CO ~25 / BTN ~44 / SB ~41).

**`pf` (<15bb) asks about a shove, not a raise** — `chart()` returns `verb = "올인"` there and the
whole UI follows that field. Don't hardcode "오픈" in text that a pf question can reach.

Personalization: `_hero_rfi(db)` groups hands with `pf_faced == "none"` (folded to hero = open
opportunity) by (position, stack bucket, combo) and `next_question` weights a combo up to ×9 when
hero's actual open rate disagrees with the chart, ×0.4 when it already agrees, ×0.15 if it came up
in the last `RECENT_SKIP` attempts. Needs `pf_faced`/`stack_bb`, so `personalized()` **auto-disables
on un-rebuilt DBs** and the drill silently falls back to uniform random — both states must keep
working, same rule as `quiz.freq_available()`. The scan is cached on hand count (`_HERO_CACHE`).

The 13×13 chart grid is fetched **only after grading** (`/api/range/chart`) — showing it earlier
leaks the answer, the same invariant as `quiz.reveal()`.

**📊 레인지 차트 (sidebar `SEL = -8`)** — its own tab, view-only: no question, no grading, just the
chart drawn GTO-tool style (each cell filled horizontally in proportion to its frequency,
`rgvGridHtml`). It lives beside `ranges.py`'s drill rather than inside it — same data, different job:
the drill asks, this one shows. It picks a chart with a **포지션 dropdown + 스택 slider** built from the imported slots. The slider
steps by **index, not bb value** (13·15·20…35 are unevenly spaced, so a value axis bunches up), and
fetched charts are cached under `pos|stack|ts` — sliding is instant on a revisit, and re-importing a
slot changes its `ts` so the stale chart cannot survive. A late response is dropped unless the view
is still on that slot. Changing position **keeps the current stack** (comparing one stack across
positions is the point of the screen), falling back to the nearest bb when that position lacks it. It lists **only imported
slots** (`state_view().custom`) — the built-in
`RFI` approximations are deliberately not browsable here, since the point is to read back what was
imported. A button swaps the cell numbers between chart frequency and **hero's own open rate**, which
outlines the cells where the two disagree (same rule as the drill's `rg-dev`). The leak invariant
above still binds across tabs: `selectRangeChart()` **clears a pending drill question**, because
otherwise leaving the drill with a question on screen would show its answer here.

`db["ranges"]["attempts"]` (capped 500) is separate from `db["quiz"]["attempts"]` on purpose: the
two modes grade on different scales and the scoreboards must not be averaged together.

**Chart slots are keyed by exact bb, not just by bucket.** `db["ranges"]["charts"]` keys are
`"POS|<slot>"` where `<slot>` is the bb number the user imported (`"UTG|13"`), so 10bb and 13bb live
side by side — `ranges.parse_stack` turns `"13"` into `("13", 13, "pf")` and is the single source of
truth for that (`grab_chart.py` calls it rather than re-deriving). Records carry `bb` + `bucket`;
legacy records keyed by a bucket (`"UTG|pf"`) are still read via `_slot_meta`, which infers both.
Grading still works in **4 buckets**, so when a bucket holds several bb charts `_pick_custom` takes
the one nearest `_BUCKET_MID` (the hero's median stack per bucket: 11/20/31/60) and prefers any bb
chart over a legacy bucket-keyed one. `chart(pos, stack)` accepts either form and an exact bb hit
wins outright; `label` shows what was imported (`UTG · 13bb`), never the bucket, so the chart the
user captured is the chart they see.

**Raise vs all-in.** A captured chart can carry `jam` (combo → all-in frequency) alongside `weights`
(combo → total action frequency); the raise share is `weights - jam`. GTO tools encode the two as
**two shades of the same red**, so `grab_chart.split_reds` 2-means the red pixels' luminance and
calls the darker cluster all-in, falling back to a single tone when the clusters are closer than
`MIN_TONE_GAP` — never hardcoding the palette. **Grading stays on the total** (`weights`): the drill
asks open-or-fold, and `jam` exists only so the chart view can draw both tones. If the drill ever
asks raise-vs-jam, that is a new question type, not a change to `_class`.

**Call (green) is different — it is graded.** A chart can also carry `call` (combo → call/limp
frequency, the green in GTO tools; `grab_chart.kind` detects it before blue so teal can't leak into
fold). `weights` stays the non-fold total (raise + jam + call), so the raise share is
`weights - jam - call`. A chart with any `call` turns the drill into **open / call / fold**
(`next_question` adds the `call` choice; no-call charts stay two-way). `_class(w, call)` and
`_grade3` judge by the chosen action's own share: ≥75% [좋음] / >25% [무난] / else [실수] — with
`call=0` this is exactly the old two-way rule, so existing charts grade the same. Personalization
compares hero's `rfi` against `weights - call`, since `rfi` counts raises only.

**Importing real GTO-tool ranges.** `ranges.parse_range` reads pasted range text leniently — plain
combo lists, `combo:freq` (0–1 or 0–100, scale inferred from the max value seen), or the same
shorthand notation the built-in charts use (`22+`, `A5s-A2s`) — and `import_chart` stores the result
at `db["ranges"]["charts"]["POS|bucket"]`, which `chart()` prefers over the built-in `RFI` table
(falling back through `_BUCKET_FALLBACK` the same way). This is how the "차트가 아니라 근사"
disclaimer above gets superseded per slot: paste a solver-accurate range in and that (pos, bucket)
grades against it instead. The 📐 오픈 레인지 탭 has a collapsible "레인지 가져오기" panel
(`rgImportHtml`/`rgImport`) for this — paste, pick pos/stack, import; imported slots list there with
a delete-back-to-builtin button. `delete_chart` removes a custom slot.

**`grab_chart.py` — 화면에서 차트 읽기 (선택 도구, 맥 전용).** GTO 위자드 무료 플랜처럼 레인지를
텍스트로 복사할 수 없을 때 쓰는 보조 CLI. `screencapture`로 화면을 찍고, 13×13 격자를 **자동으로
찾아** 셀마다 색이 가로로 차지한 비율을 세서 빈도를 계측한 뒤 `POST /api/range/import`로 보낸다
(앱이 떠 있어야 한다 — 저장·클라우드 푸시를 앱이 하게 해서 DB를 직접 건드리지 않는다. 직접 쓰면
다음 실행 때 클라우드 pull에 덮어쓰인다). 표준 라이브러리만 쓴다: 이미지는 맥 기본 `sips`로 BMP로
바꿔 직접 디코드. 색은 팔레트를 고정하지 않고 **파랑=폴드 / 빨강=액션**으로만 보므로 테마가 바뀌어도,
빨강이 두 톤(레이즈/올인)이어도 동작한다 — 단 **두 톤은 합쳐져 '오픈 빈도' 하나가 된다**
(차트 모델이 조합당 빈도 하나뿐이라 그렇다). 셀 경계는 구분선을 검출해서 잡는다. 등분으로 떨어지면
9%짜리 얇은 띠가 날아가므로 경고를 띄운다. 스택 인자는 GTO 툴에 적힌 **bb 숫자를 그대로** 받아
`store._stack_bucket`으로 버킷을 정한다(기준을 두 벌 만들지 않는다). 버킷은 4개뿐이라 40bb와 100bb가
같은 `deep` 슬롯을 공유한다 — 덮어쓰게 되면 기존 차트의 출처·오픈%를 경고로 먼저 보여준다.

**포지션은 두 체계다.** 가져오는 차트(grab_chart·가져오기 패널)는 8맥스 GTO 툴 이름
`ranges.POS_8MAX` = UTG UTG1 LJ HJ CO BTN SB BB만 받는다. 내장 `RFI` 차트는 옛 체계(UTG/MP/CO/BTN/SB,
SB(BTN))를 그대로 쓰고, 8맥스 이름엔 내장 차트가 없다(가져온 것만 있다). 핸드 기록의 `hero_pos`는
`convert.assign_positions`가 준 UTG/MP1/MP2/MP3이라, `_hero_rfi`가 `_pos_8max`로 테이블 인원(`players`)을
보고 8맥스 이름으로 바꿔 **두 이름 모두에** 센다 — 그래서 가져온 LJ 차트에도 내 오픈률이 겹쳐진다.
환산 기준은 **뒤에 남은 인원 수**(`_BEHIND_8MAX`) 하나다: 오픈 레인지를 정하는 건 그 수뿐이라 테이블
인원이 달라도 같으면 같은 자리다. **UTG도 예외가 아니다** — 7인 UTG는 뒤에 6명이라 8맥스 UTG+1이고,
6인 UTG는 LJ다. 8맥스 UTG(뒤에 7명)는 8인 테이블에서만 나온다. 이걸 빼먹고 UTG를 그대로 두면,
7·6맥스만 치는 사람의 UTG 기록이 **제 자리보다 2~3칸 타이트한 차트**에 겹쳐져 "차트대로 잘 치고
있다"로 보인다 (실측: 내 UTG 오픈 17.1% vs 8맥스 UTG 차트 17.2% — 맞는 자리인 UTG1/LJ 기준으로는
19.4~22.1%라 5p 가까이 타이트했다). CO·BTN·SB·BB는 버튼 기준이라 인원과 무관하게 이름이 그대로다.

API: `GET /api/range/state` · `/api/range/next?pos=&stack=` · `/api/range/chart?pos=&stack=` ·
`POST /api/range/grade` (plain JSON, no streaming) · `/api/range/import`
(`{pos, stack, text, source}`) · `/api/range/delete-chart` (`{pos, stack}`).

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
