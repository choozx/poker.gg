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

Modules, strict dependency direction `convert ← store ← {bankroll, quiz, ranges} ← coach ← gui`:

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
- **`coach.py`** — the 💬 AI 코치 chat: builds the per-message context + stores chats (see below).
  The only module that imports both `quiz` and `ranges` (it reads from them, never the reverse).

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
the drill asks, this one shows. It picks a chart with a **GTO-tool-style action card bar + 스택 slider**.
The bar (`rgvBarHtml`) is one card per seat in action order; you walk the preflop tree by clicking
actions (UTG 레이즈 → the next seat's card shows its defense chart, a card's 폴드 → the next seat, a later
card → "everyone between folds"). It is **only a view of `(pos, vs)`** — no separate tree state: `vs`
empty = folded to `pos` (open chart), `vs` set = `vs` opened and the rest folded (defense chart). The
current seat's card shows the chart's overall action split. Data stops at "one open + one response",
so 3벳/콜/림프 continuations are rendered but disabled (`NA` tooltip), and folding to BB is a walk.
Navigation (`rgvGo`) **keeps the slider's stack exactly** — if that spot has no chart there it shows the
missing-chart card with its capture command instead of jumping to another stack. The slider's
axis is the **union of every imported slot's stack** (`rgvAxis`), not just the current spot's — so a
spot with a single chart (e.g. one BB-defense stack) still gets a slider, ticks it lacks are dimmed,
and picking one shows "차트가 아직 없습니다" with the exact `grab_chart.py` command (`v.miss`). The slider
steps by **index, not bb value** (13·15·20…35 are unevenly spaced, so a value axis bunches up), and
fetched charts are cached under `pos|stack|ts` — sliding is instant on a revisit, and re-importing a
slot changes its `ts` so the stale chart cannot survive. A late response is dropped unless the view
is still on that slot, and a failed fetch is remembered (`failKey`) so the "load if not loaded" check in
`renderRangeView` can't refetch in a loop. It shows **only imported slots** (`state_view().custom`) — the built-in
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

**방어 차트 (vs 오픈) — 슬롯 키에 오프너가 붙는다.** BB는 오픈 기회가 없어(폴드되면 워크) 레인지가
"누가 오픈했을 때"로만 존재한다. 그래서 키가 `"BB|20|vsBTN"`처럼 세 칸이 되고(`_ckey`/`_split_key`
— 키를 `partition("|")`로 직접 쪼개지 말 것, 세 번째 칸이 슬롯에 붙어 버린다), 레코드도 `vs`를 든다.
상대 없는 키는 예전 그대로 오픈 차트라 기존 데이터는 손댈 게 없다. BB만이 아니라 아무 자리 vs 앞자리
오프너가 같은 틀이다(`parse_vs`: 오프너는 나보다 먼저 액션하는 자리, BB는 상대 필수). 모양은 같다 —
`weights` = 비폴드 합계(3벳+올인+콜), `jam`·`call`은 그 몫 — 바뀌는 건 뜻뿐이라 `chart()`가 `verb`
(`"3벳"`)와 `call_name`(`"콜"`, 오픈 차트는 `"콜(림프)"`)을 실어 보내고 **UI 문구는 전부 그 필드를
따른다**. 내장 방어 차트는 없다(가져온 것만). 드릴은 `_contexts`가 (포지션, 버킷)마다 오픈 1 + 상대별
방어 문제를 만들되, 방어 쪽은 **몫을 나눠 합쳐서 오픈 하나만큼**만 나오게 한다(상대 7명분을 넣어도 BB가
7배로 쏠리지 않게). 실전 기록은 `convert.hand_meta`의 **`pf_opener`**(오픈 한 번만 받았을 때 오프너
자리, 림프·콜러·3벳 팟은 None) 기준 `_hero_vs`로 세고, 비율은 **방어(콜+3벳) 비율**이라 차트 합계와
비교한다(오픈 차트는 rfi가 레이즈만 세서 `weights - call`과 비교). 두 자리 모두 `_pos_8max`로 옮기며
**원래 이름(MP1…)을 넘겨야** 한다 — `_norm_pos`로 MP로 접은 뒤엔 몇 번째 자리인지 못 센다.
`pf_opener`는 새 필드라 `vs_personalized()`가 꺼지면 방어 쪽 겹쳐 보기·가중치만 꺼진다(오픈 쪽은 그대로).
"차트와 어긋난 칸" 판정은 `chart_view`가 셀마다 `dev`로 내려준다 — 방어 차트는 콜 칸도 '액션 칸'이라
규칙이 달라서, 프론트 두 곳에 규칙을 복사해 두지 않는다.

**상대가 어떻게 들어왔나 — `vs` 꼬리 (`ranges.vs_parts` → (오프너, `raise`|`allin`|`limp`)).** 꼬리 없음 =
오픈 레이즈를 받음, `-allin` = 오픈 올인을 받음, `-limp` = **SB 림프를 받은 BB**(`parse_vs`가 SB→BB만 허용 —
MTT 트리에서 오픈 림프는 SB뿐). 림프를 받은 BB엔 폴드가 없어 액션이 레이즈(아이솔)/체크다: `import_chart`가
**비폴드에서 콜 몫(체크가 초록으로 읽힌 것)을 빼서** 레이즈만 남기고, `chart()`가 `fold_name: "체크"`를 실어
보내 드릴 버튼·채점 문구·범례가 '폴드' 대신 '체크'를 쓴다. 실전 기록은 `convert.hand_meta`의 **`pf_limper`**
(레이즈 없이 딱 한 명만 림프하고 히어로에게 왔을 때 그 자리)로 세고, 비율은 아이솔 레이즈 비율이다.
`grab_chart --watch`는 SB 오픈 차트에 림프가 있는 스택에만 'SB 림프 vs BB' 칸을 넣는다(`plan(limps=…)`).

**오픈 올인을 받은 방어 차트 — `vs`에 `-allin` 꼬리.** 오픈 레이즈를 받은 것과 오픈 올인을 받은 것은
GTO 툴에서도 다른 노드이고 레인지가 완전히 다르다(남은 액션이 콜/폴드뿐). 그래서 `vs`를 `"UTG"`(레이즈) /
`"UTG-allin"`(올인)으로 가른다(`ranges.ALLIN`, `vs_parts`, `_norm_vs` — **`vs`에 `_norm_pos`를 직접 쓰지
말 것**, 꼬리까지 대문자가 되어 키가 갈린다). 꼬리 없는 기존 키는 그대로 레이즈 차트라 옮길 데이터가 없다.
올인을 받은 차트는 `import_chart`가 **비폴드 전부를 `call`로** 저장한다 — GTO 툴이 이 콜을 무슨 색으로
그리든 '3벳'으로 읽히지 않게. `chart()`가 `allin: True`와 `verb: "콜"`을 실어 보내고, 드릴은 콜/폴드
2지선다(레이즈 선택지 없음 — 프론트도 `choices`대로만 버튼을 그린다), 차트·카드 바는 레이즈 칸을 숨긴다.
실전 기록은 `convert.hand_meta`의 **`pf_opener_allin`**(그 단독 오픈이 올인이었나)으로 가른다 — 이 필드가
없는 DB(이 기능 전에 rebuild)에선 올인 오픈을 받은 핸드가 레이즈 쪽에 섞여 세지고, 올인 쪽은 비어 있다.
카드 바의 '올인' 칩은 그 자리의 오픈 차트(그 스택)에 올인이 있을 때만 뜨고, `grab_chart --watch`의 순서표도
같은 기준(`plan(jams=…)`)으로 올인을 받는 칸을 넣는다 — 올인 오픈이 없는 스택에 빈 칸을 만들지 않게.

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

**`grab_chart.py --watch` — 감시 모드 (연달아 캡처).** 목표가 스택 8 × (오픈 7 + 방어 28) = 280장이라
한 장씩 커맨드를 치면 시간 대부분이 '터미널로 가서 인자 고치기'에 든다. 감시 모드는 순서표(`plan`)를 만들어
**이미 들어 있는 슬롯은 건너뛰고**, 메인 디스플레이를 `POLL_SEC`마다 BMP로 바로 찍어(`capture_fast`, sips를
건너뛰어 0.15초) 첫 장에서 잡은 격자 위치를 재사용해 읽는다. **새 차트가 `STABLE_POLLS`번 연달아 같으면**
순서표의 다음 칸으로 저장한다. 순서는 GTO 툴에서 클릭해 가는 길을 따른다: 스택마다 UTG 오픈 → (UTG 레이즈)
UTG1·LJ…BB의 방어 → UTG1 오픈 → …. 지켜야 할 것들:
- 이 도구는 화면 글자를 못 읽어 **지금 차트가 어느 칸인지 스스로 모른다** — 순서는 사용자가 지킨다. 그래서
  다음 칸을 크게 띄우고, 뻔히 어긋난 모양(`suspicious`: SB가 아닌 오픈 차트에 콜, BB 방어에 콜 없음)은
  저장하지 않고 멈추며(`held`), `u`로 방금 저장한 걸 지운다(`delete-chart`).
- **시작 화면과 되돌리기·건너뛰기 직후 화면은 '이미 본 것'으로 친다**(`last_sig` / `reseed`). 안 그러면
  화면은 그대로인데 그 차트가 방금 열린 칸으로 곧장 저장된다 — 시뮬레이션에서 실제로 났던 버그다.
  같은 차트가 두 칸 연달아 정답인 드문 경우는 Enter로 강제 저장.
- GTO 툴을 대신 클릭하지 않는다 (사이트 자동 수집은 이용약관 문제). 소리 알림도 넣지 않는다 (사용자 요청).
- 격자를 못 찾는 건 `GridNotFound`(`SystemExit` 하위)로 올린다 — 한 장 모드는 기존처럼 종료 메시지가 되고,
  감시 모드는 잡아서 다음 폴링을 기다린다.

**`export_chart.py` — 폰용 내보내기 (선택 도구).** 가져온 차트를 전부 인라인한 **HTML 한 장**을
쓴다. 서버도 네트워크도 타지 않으므로 폰에 옮겨 열면 그대로 돌고, '홈 화면에 추가'하면 앱처럼
보인다 — 56장이 74KB(gzip 7KB)뿐이라 APK로 감쌀 이유가 없다(감싸도 이 파일을 띄우는 WebView다).
셀은 `[합계, 올인, 콜]` 0~100 정수로만 싣고(폰 화면에서 소수점은 의미가 없다), `--hero`면 실전
기록도 함께 넣는다. **기본 출력 경로는 레포 밖(`~/Desktop`)이고 결과물은 `.gitignore`에 있다** —
이 repo는 공개라 GTO 툴에서 가져온 레인지가 담긴 파일을 커밋하면 인터넷에 그대로 공개된다.

API: `GET /api/range/state` · `/api/range/next?pos=&stack=` · `/api/range/chart?pos=&stack=&vs=` ·
`POST /api/range/grade` (plain JSON, no streaming; `vs` from the question) · `/api/range/import`
(`{pos, stack, text, source, vs}`) · `/api/range/delete-chart` (`{pos, stack, vs}`). `vs` empty = 오픈 차트.

### 💬 AI 코치 (`coach.py`) — 내 기록을 근거로 대화

Sidebar `SEL = -9`. The other AI features are one-shot; this one is a conversation. Both backends
are single-call (`stream(system, user)`), so continuity is faked: `build_prompt` re-sends the last
`HISTORY_TURNS` turns every time (past replies quoted with `>` so their `##` headings can't blur the
prompt's own sections). Each message also carries:

- **`profile_text(db)`** — "내 플레이 요약", recomputed per message from **frozen meta fields only**
  (~0.3s on 100k hands, ~3K chars): VPIP/PFR, 최근 `RECENT_HANDS` vs 전체, 포지션별 칩 EV, 오픈율 vs
  차트 and BB 방어율 vs 가져온 방어 차트 (both **weighted by the combos hero was actually dealt**, so a
  small sample's card luck isn't read as a leak), `quiz.leak_spots`, `store.leak_report`, drill scores.
  This is what stops the AI from inventing numbers — `COACH_SYSTEM_PROMPT` tells it to quote only these.
  On un-rebuilt DBs the chart sections say *why* they're empty, so "no data" isn't read as "no leak".
- **`#핸드번호` refs** — the hand's `render_markdown` + its stored `analysis`. The UI only shows the
  last 6 digits, so `find_hand` accepts a **unique** suffix. Refs anywhere in the history window are
  re-attached (max `MAX_REF_HANDS`), so a follow-up like "그럼 턴은?" still sees the hand.

`db["coach"]["chats"]` is capped (`MAX_CHATS` 20 by last use, `MAX_MESSAGES` 60 each) — cloud-synced
DB, same reason as the quiz caps. A question+answer pair is saved **only on a clean finish**; the client
re-reads the chat afterwards to tell whether it was saved and offers 다시 보내기 if not. The chat id is
minted server-side and returned in `X-Chat-Id` before the body (via `_stream_ai(headers=…)`), along
with `X-Coach-Refs` / `X-Coach-Missing`. The view re-renders only the message list while streaming —
re-rendering the textarea would eat what the user is typing. The hand viewer's "💬 이 핸드로 대화"
opens a new chat with `#id` prefilled.

API: `GET /api/coach/chats` · `/api/coach/chat?id=` · `POST /api/coach/send` (`{chat_id, text}`,
streams) · `/api/coach/delete` (`{id}`).

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
`rfi_opp`, `pf_action`, `pf_faced`, `pf_opener`, `pf_opener_allin`, `pf_limper`, `stack_bb`, `net_bb`, `review`, `hero_pos`, …) **once** and stores them in
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
