#!/usr/bin/env python3
"""🃏 오픈 레인지 드릴 — 프리플랍 RFI 차트를 상대로 한 속사 문제.

`quiz.py`(핸드 리뷰 문제)와 같은 위상의 형제 모듈이지만 성격이 정반대다:

  - quiz.py  : 출제는 로컬, **채점은 AI** (정답이 하나로 정해지지 않는 포스트플랍)
  - ranges.py: 출제도 채점도 **전부 로컬** — 정답이 차트에 박혀 있으므로 AI 호출 0회

'폴드로 돌아온 상황에서 이 핸드를 오픈하나?'만 묻는다. 포지션 × 스택버킷마다 오픈
레인지를 하나씩 들고 있고, 고른 답을 차트와 대조해 즉시 채점한다.

## 차트에 대한 정직한 설명

여기 박혀 있는 레인지는 **솔버 실시간 출력이 아니라 공개된 MTT 참고 차트의 근사**다.
이 앱은 표준 라이브러리 전용이라 솔버를 돌리거나 외부에서 받아올 방법이 없다. 안티
크기·필드 구성·ICM에 따라 경계는 얼마든지 움직이므로, 경계 핸드는 `mix`(혼합)로 따로
표시해 **오픈이든 폴드든 [무난]**으로 채점한다. 확실한 구간만 [좋음]/[실수]가 된다.
차트를 고칠 일이 생기면 아래 표기법 문자열만 고치면 된다 — 파서가 169조합으로 편다.

## 내 실전 기록과의 연결

`pf_faced == "none"`(폴드 투 히어로 = 오픈 기회)인 핸드를 포지션×스택×조합으로 묶어,
**차트와 어긋나게 친 조합을 우선 출제**한다 (`_hero_rfi`). 범용 차트 암기가 아니라 내
리크를 때리는 드릴이 되게 하는 부분. `pf_faced`/`stack_bb`가 없는 구 DB에서는 이 가중치가
자동으로 꺼지고 균등 무작위 출제로 떨어진다 — 두 상태 모두 동작해야 한다.

의존 방향: convert ← store ← ranges ← gui (quiz.py와 같은 위상, 서로 참조하지 않는다)
"""

import random
import re
import time

import store

RANKS = "AKQJT98765432"
_RI = {r: i for i, r in enumerate(RANKS)}     # A=0 … 2=12 (작을수록 높은 랭크)

MAX_ATTEMPTS = 500    # DB에 남기는 응시 기록 수 (DB는 클라우드 동기화 대상 — 작게 유지)
RECENT_SKIP = 24      # 최근 이만큼 안에 나온 조합은 다시 잘 안 나오게 가중치를 낮춘다


# ---------------------------------------------------------------------------
# 레인지 표기법 파서 — "22+, A2s+, KTs+, AQo+" → 169조합 집합
# ---------------------------------------------------------------------------

def _label(hi, lo, suited):
    """랭크 두 개 → store._combo와 **같은 표기**의 조합 라벨 (AA / AKs / AKo)."""
    if hi == lo:
        return hi + lo
    if _RI[hi] > _RI[lo]:
        hi, lo = lo, hi
    return hi + lo + ("s" if suited else "o")


def _pair_span(a, b):
    i, j = sorted((_RI[a], _RI[b]))
    return {RANKS[k] * 2 for k in range(i, j + 1)}


def _expand_token(tok):
    if "-" in tok:                                  # 77-TT / A5s-A2s / K5o-K7o
        a, b = tok.split("-", 1)
        if len(a) == 2 and len(b) == 2:
            return _pair_span(a[0], b[0])
        if len(a) != 3 or len(b) != 3 or a[0] != b[0] or a[2] != b[2]:
            raise ValueError(f"레인지 표기 오류: {tok}")
        i, j = sorted((_RI[a[1]], _RI[b[1]]))
        return {_label(a[0], RANKS[k], a[2] == "s") for k in range(i, j + 1)}
    plus = tok.endswith("+")
    if plus:
        tok = tok[:-1]
    if len(tok) == 2:                               # 77 / 77+
        if tok[0] != tok[1]:
            raise ValueError(f"페어 표기 오류: {tok}")
        return _pair_span("A", tok[0]) if plus else {tok[0] * 2}
    if len(tok) != 3 or tok[2] not in "so":
        raise ValueError(f"조합 표기 오류: {tok}")
    hi, lo, suit = tok[0], tok[1], tok[2] == "s"
    if plus:                                        # A2s+ → A2s..AKs (탑 랭크 고정)
        return {_label(hi, RANKS[k], suit) for k in range(_RI[hi] + 1, _RI[lo] + 1)}
    return {_label(hi, lo, suit)}


def expand(notation):
    """표기법 문자열 → 조합 라벨 집합."""
    out = set()
    for tok in notation.replace(" ", "").split(","):
        if tok:
            out |= _expand_token(tok)
    return out


def combo_weight(combo):
    """그 조합이 차지하는 실제 카드 콤보 수 (페어 6 / 수딧 4 / 오프수딧 12)."""
    if len(combo) == 2:
        return 6
    return 4 if combo.endswith("s") else 12


def all_combos():
    """169개 조합 라벨 (그리드 순서: 위/왼쪽이 높은 랭크, 우상단 수딧)."""
    out = []
    for i in range(13):
        for j in range(13):
            hi, lo = RANKS[min(i, j)], RANKS[max(i, j)]
            out.append(_label(hi, lo, i < j) if i != j else RANKS[i] * 2)
    return out


# ---------------------------------------------------------------------------
# 차트 — 포지션 × 스택버킷별 오픈(=RFI) 레인지
#
# open: 확실히 오픈하는 구간, mix: 경계(오픈/폴드 어느 쪽도 [무난])
# 스택버킷은 store._stack_bucket과 같은 키를 쓴다: pf(<15) / short(15–25) /
# mid(25–40) / deep(40+). pf 구간은 '레이즈'가 아니라 **푸시(올인)** 레인지다.
# MP1/MP2/MP3은 _norm_pos로 MP 하나에 묶인다 (앱 전체가 그렇게 다룬다).
# ---------------------------------------------------------------------------

RFI = {
    "UTG": {
        "deep": ("22+, A6s+, A5s-A2s, KTs+, QTs+, JTs, T9s, 98s, AQo+, AJo, KQo",
                 "K9s, Q9s, J9s, 87s, KJo, ATo"),
        "mid": ("22+, A6s+, A5s-A2s, KTs+, QTs+, JTs, T9s, 98s, AQo+, KQo",
                "K9s, Q9s, J9s, 87s, AJo, KJo"),
        "short": ("22+, A8s+, A5s-A2s, KTs+, QJs, JTs, AJo+, KQo",
                  "A7s-A6s, K9s, Q9s, T9s, 98s, ATo, KJo"),
        "pf": ("22+, A2s+, K9s+, QTs+, JTs, T9s, ATo+, KJo+",
               "K8s-K7s, Q9s, J9s, 98s, A9o, KTo, QJo"),
    },
    "MP": {
        "deep": ("22+, A2s+, K9s+, Q9s+, J9s+, T8s+, 97s+, 87s, 76s, ATo+, KQo",
                 "K8s, Q8s, J8s, T7s, 86s, 65s, A9o, KJo, QJo"),
        "mid": ("22+, A2s+, K9s+, Q9s+, J9s+, T9s, 98s, ATo+, KQo",
                "K8s, Q8s, J8s, T8s, 87s, 76s, A9o, KJo, QJo"),
        "short": ("22+, A2s+, K9s+, Q9s+, JTs, T9s, ATo+, KJo+",
                  "K8s, Q9s, J9s, 98s, 87s, A9o, KTo, QJo"),
        "pf": ("22+, A2s+, K7s+, Q9s+, J9s+, T9s, A9o+, KTo+, QJo",
               "K6s, Q8s, J8s, 98s, 87s, A8o, K9o, QTo, JTo"),
    },
    "CO": {
        "deep": ("22+, A2s+, K5s+, Q7s+, J7s+, T7s+, 97s+, 86s+, 75s+, 65s, 54s, "
                 "A9o+, KJo+, QJo",
                 "K4s, Q6s, J6s, T6s, 64s, 53s, 43s, A8o, KTo, QTo, JTo, T9o"),
        "mid": ("22+, A2s+, K7s+, Q8s+, J7s+, T8s+, 97s+, 87s, 76s, 65s, "
                "A9o+, KJo+, QJo",
                "K6s, Q7s, J6s, T7s, 86s, 75s, 54s, A8o, KTo, QTo, JTo"),
        "short": ("22+, A2s+, K7s+, Q8s+, J8s+, T8s+, 98s, 87s, A8o+, KJo+, QJo",
                  "K6s, Q7s, J7s, T7s, 76s, 65s, KTo, QTo, JTo"),
        "pf": ("22+, A2s+, K5s+, Q8s+, J8s+, T8s+, 97s+, 87s, 76s, A7o+, KTo+, QJo",
               "K4s, Q7s, J7s, T7s, 86s, 65s, 54s, A6o, K9o, QTo, JTo, T9o"),
    },
    "BTN": {
        "deep": ("22+, A2s+, K2s+, Q4s+, J6s+, T6s+, 95s+, 85s+, 74s+, 63s+, 53s+, 43s, "
                 "A2o+, K7o+, Q9o+, J9o+, T9o",
                 "Q3s-Q2s, J5s-J4s, T5s, 94s, 84s, 73s, 62s, 52s, 42s, 32s, "
                 "K6o-K5o, Q8o, J8o, T8o, 98o"),
        "mid": ("22+, A2s+, K2s+, Q4s+, J6s+, T6s+, 96s+, 86s+, 75s+, 64s+, 54s, "
                "A2o+, K7o+, Q9o+, J9o+, T9o",
                "Q3s-Q2s, J5s, T5s, 95s, 85s, 74s, 63s, 53s, 43s, "
                "K6o, Q8o, J8o, T8o, 98o"),
        "short": ("22+, A2s+, K2s+, Q4s+, J6s+, T7s+, 96s+, 86s+, 75s+, 64s+, 54s, "
                  "A2o+, K8o+, Q9o+, J9o+, T9o",
                  "Q3s, J5s, T6s, 95s, 85s, 74s, 53s, 43s, "
                  "K7o, Q8o, J8o, 98o"),
        "pf": ("22+, A2s+, K2s+, Q4s+, J6s+, T6s+, 96s+, 86s+, 75s+, 65s, 54s, "
               "A2o+, K7o+, Q9o+, J9o+, T9o",
               "Q3s-Q2s, J5s, T5s, 95s, 85s, 74s, 64s, 53s, 43s, "
               "K6o-K5o, Q8o, J8o, T8o, 98o"),
    },
    "SB": {
        "deep": ("22+, A2s+, K2s+, Q2s+, J5s+, T6s+, 95s+, 85s+, 75s+, 64s+, 54s, "
                 "A2o+, K8o+, Q9o+, JTo",
                 "J4s-J2s, T5s, 94s, 84s, 74s, 63s, 53s, 43s, "
                 "K7o-K5o, Q8o, J9o, T9o, 98o"),
        "mid": ("22+, A2s+, K2s+, Q2s+, J5s+, T6s+, 96s+, 86s+, 75s+, 64s+, 54s, "
                "A2o+, K8o+, Q9o+, J9o+, JTo",
                "J4s-J2s, T5s, 95s, 85s, 74s, 63s, 53s, 43s, "
                "K7o-K6o, Q8o, J8o, T9o"),
        "short": ("22+, A2s+, K2s+, Q3s+, J6s+, T6s+, 96s+, 86s+, 75s+, 65s, 54s, "
                  "A2o+, K8o+, Q9o+, J9o+, T9o",
                  "Q2s, J5s, T5s, 95s, 85s, 74s, 64s, 53s, "
                  "K7o, Q8o, J8o, 98o"),
        "pf": ("22+, A2s+, K2s+, Q3s+, J5s+, T6s+, 95s+, 85s+, 75s+, 64s+, 54s, "
               "A2o+, K6o+, Q8o+, J8o+, T8o, 98o",
               "Q2s, J4s, T5s, 94s, 84s, 74s, 63s, 53s, 43s, "
               "K5o-K4o, Q7o, J7o, T7o, 97o, 87o"),
    },
    # 헤즈업(버튼=SB). 스택이 어떻든 거의 다 오픈하므로 한 장으로 충분하다.
    "SB(BTN)": {
        "deep": ("22+, A2s+, K2s+, Q2s+, J2s+, T2s+, 92s+, 82s+, 72s+, 62s+, 52s+, "
                 "42s+, 32s, A2o+, K2o+, Q2o+, J4o+, T5o+, 95o+, 85o+, 75o+, 64o+, 54o",
                 "J3o-J2o, T4o-T3o, 94o-93o, 84o-83o, 74o-73o, 63o, 53o"),
    },
}

# 차트가 없는 버킷은 가장 가까운 것으로 대체 (헤즈업은 한 장뿐이다)
_BUCKET_FALLBACK = {"deep": ["deep", "mid", "short", "pf"],
                    "mid": ["mid", "deep", "short", "pf"],
                    "short": ["short", "mid", "pf", "deep"],
                    "pf": ["pf", "short", "mid", "deep"]}

# 가져오는 차트(grab_chart·가져오기 패널)의 포지션 — 8맥스 GTO 툴 이름 그대로.
# 내장 차트(위 RFI)는 MP 하나로 묶은 옛 체계라 여기 없는 MP·SB(BTN)은 내장 전용이다.
POS_8MAX = ["UTG", "UTG1", "LJ", "HJ", "CO", "BTN", "SB", "BB"]
POS_ORDER = ["UTG", "UTG1", "LJ", "HJ", "MP", "CO", "BTN", "SB", "BB", "SB(BTN)"]
STACK_ORDER = ["pf", "short", "mid", "deep"]
STACK_LABEL = {"pf": "<15bb", "short": "15–25bb", "mid": "25–40bb", "deep": "40bb+"}
POS_KO = {"UTG": "UTG (얼리)", "UTG1": "UTG+1 (얼리)", "LJ": "LJ (로우잭)",
          "HJ": "HJ (하이잭)", "MP": "MP (미들)", "CO": "CO (컷오프)",
          "BTN": "BTN (버튼)", "SB": "SB (스몰블라인드)", "BB": "BB (빅블라인드)",
          "SB(BTN)": "SB/BTN (헤즈업)"}

_CHART_CACHE = {}

# 오픈을 받은 방어 차트(vs 오픈)에서 빨강이 뜻하는 액션. 오픈 차트의 '오픈/올인' 자리에 들어간다.
VS_VERB = "3벳"

# 상대(오프너)가 **어떻게 들어왔나**를 이름 뒤 꼬리로 단다. 셋 다 GTO 툴에서 서로 다른 노드고
# 레인지가 완전히 다르다:
#   'UTG'       오픈 레이즈를 받음 → 3벳/콜/폴드   (꼬리 없음 — 예전 키 그대로라 기존 데이터는 그대로)
#   'UTG-allin' 오픈 올인을 받음   → 콜/폴드
#   'SB-limp'   SB 림프를 받은 BB  → 레이즈/체크   (폴드가 없다 — 공짜로 플랍을 볼 수 있으니)
ALLIN, LIMP = "-allin", "-limp"
_TAILS = {ALLIN: "allin", LIMP: "limp"}
KIND_NAME = {"raise": "오픈", "allin": "올인", "limp": "림프"}
_KIND_ORDER = {"raise": 0, "allin": 1, "limp": 2}


def vs_parts(vs):
    """'UTG-allin' → ('UTG', 'allin') / 'SB-limp' → ('SB', 'limp') / 'UTG' → ('UTG', 'raise') /
    None → (None, None)."""
    if not vs:
        return None, None
    vs = str(vs)
    for tail, kind in _TAILS.items():
        if vs.lower().endswith(tail):
            return vs[:-len(tail)], kind
    return vs, "raise"


def _norm_vs(vs):
    """상대 표기 정규화 — 포지션 부분만 _norm_pos (꼬리까지 대문자가 되면 키가 갈린다)."""
    op, kind = vs_parts(vs)
    op = _norm_pos(op) if op else None
    if not op:
        return None
    return op + next((t for t, k in _TAILS.items() if k == kind), "")


def _ckey(pos, slot, vs=None):
    """차트 저장 키. 오픈 차트는 'UTG|20', 방어 차트는 'BB|20|vsBTN' (BTN 오픈에 대한 BB)."""
    return f"{pos}|{slot}|vs{vs}" if vs else f"{pos}|{slot}"


def _split_key(key):
    """저장 키 → (포지션, 슬롯, 상대). 'BB|20|vsBTN' → ('BB', '20', 'BTN'), 오픈 차트는 상대 None."""
    pos, _, rest = key.partition("|")
    slot, _, vs = rest.partition("|")
    return pos, slot, (vs[2:] if vs.startswith("vs") else vs) or None


def parse_vs(pos, vs):
    """(포지션, 상대 인자) → (상대 이름 또는 None, 오류 문구 또는 None).

    방어 차트의 상대는 **나보다 먼저 액션하는 자리**여야 한다 (BB는 UTG~SB 전부).
    BB는 오픈 기회가 없으므로(폴드되면 워크) 상대 없는 BB 차트는 받지 않는다."""
    pos = _norm_pos(pos)
    vs = _norm_vs(vs)
    if not vs:
        if pos == "BB":
            return None, "BB는 오픈 상황이 없습니다 — 상대(오프너)를 지정하세요 (예: vs BTN)"
        return None, None
    op, kind = vs_parts(vs)
    if op not in POS_8MAX or pos not in POS_8MAX:
        return None, f"알 수 없는 상대 포지션: {op} ({' / '.join(POS_8MAX[:-1])} 중 하나)"
    if POS_8MAX.index(op) >= POS_8MAX.index(pos):
        return None, f"오프너는 {pos}보다 먼저 액션하는 자리여야 합니다 (받은 값: {op})"
    if kind == "limp" and (op, pos) != ("SB", "BB"):
        # 오픈 림프는 SB만 한다 (GTO 툴 MTT 트리) — 림프를 받는 건 BB뿐
        return None, "림프를 받은 차트는 BB vs SB 림프뿐입니다"
    return vs, None


def _norm_pos(pos):
    """MP1/MP2/MP3 → MP (차트 조회용). quiz._norm_pos와 같은 규칙.
    사용자 입력도 여기를 지나므로 소문자·'UTG+1' 표기도 받는다."""
    if not pos:
        return None
    pos = str(pos).strip().upper().replace("UTG+1", "UTG1")
    return "MP" if pos.startswith("MP") else pos


# 뒤에 남은 인원 수 → 8맥스 자리 이름. 오픈 레인지를 정하는 건 '내 뒤에 몇 명 남았나'
# 하나뿐이라, 테이블 인원이 달라도 이 수가 같으면 같은 자리다.
_BEHIND_8MAX = {7: "UTG", 6: "UTG1", 5: "LJ", 4: "HJ", 3: "CO", 2: "BTN", 1: "SB", 0: "BB"}


def _pos_8max(pos, players):
    """핸드 기록의 포지션(UTG/MP1…/CO) → 8맥스 이름. 가져온 차트에 내 실전 기록을
    겹쳐 보려면 같은 이름이어야 해서다.

    **뒤에 남은 인원 수로 옮긴다**: 7인 테이블의 UTG는 뒤에 6명이라 8맥스 UTG+1과
    같은 자리고, 6인 UTG는 LJ다. 8맥스 UTG(뒤에 7명)는 8인 테이블에서만 나온다.
    CO·BTN·SB·BB는 버튼 기준이라 인원과 무관하게 이름이 그대로다.
    `players`가 없는 옛 레코드는 자리를 셀 수 없어 손대지 않는다 (6인 MP만 확정적)."""
    if not pos or not (pos == "UTG" or pos.startswith("MP")):
        return pos
    try:
        n = int(players)
    except (TypeError, ValueError):
        return "HJ" if pos == "MP" else pos
    # 프리플랍 행동 순서에서 몇 번째인가 (UTG=0, MP1=1 …). 6인 테이블의 MP는 1번.
    idx = 0 if pos == "UTG" else (1 if pos == "MP" else int(pos[2:]))
    return _BEHIND_8MAX.get(n - 1 - idx, pos)


# 빈도 → 판정. 0.75 이상이면 확실한 오픈, 0.25 이하면 확실한 폴드, 사이는 경계(혼합).
OPEN_HI, FOLD_LO = 0.75, 0.25


def _dist(w, call=0.0):
    """조합 빈도 → 액션별 몫. `w`는 폴드가 아닌 액션 합계, `call`은 그중 콜(림프) 몫."""
    call = min(call, w)
    return {"open": w - call, "call": call, "fold": 1.0 - w}


def _class(w, call=0.0):
    """확실한(75% 이상) 액션이 있으면 그 액션, 없으면 "mix". 콜이 없는 차트에선
    예전 규칙(w ≥ 0.75 오픈 / w ≤ 0.25 폴드 / 사이는 경계)과 정확히 같다."""
    d = _dist(w, call)
    best = max(d, key=d.get)
    return best if d[best] >= OPEN_HI else "mix"


def _builtin_weights(pos, bucket_used):
    """내장 표기법 차트 → 빈도 dict. 내장 차트의 경계는 빈도 0.5로 본다."""
    open_s, mix_s = RFI[pos][bucket_used]
    opens = expand(open_s)
    mixes = expand(mix_s) - opens         # 표기가 겹치면 오픈이 이긴다
    w = {c: 1.0 for c in opens}
    w.update({c: 0.5 for c in mixes})
    return w


def _summary(weights):
    """(빈도 가중 오픈 비율 %, 경계 구간이 차지하는 비율 %)."""
    pct = sum(w * combo_weight(c) for c, w in weights.items()) / 1326 * 100
    mix = sum(combo_weight(c) for c, w in weights.items()
              if FOLD_LO < w < OPEN_HI) / 1326 * 100
    return round(pct, 1), round(mix, 1)


def _charts(db):
    return ((db or {}).get("ranges") or {}).get("charts") or {}


# 버킷 하나에 bb별 차트가 여러 장 들어올 수 있다 (10bb·13bb 둘 다 pf). 드릴은 버킷
# 단위로 묻기 때문에 그중 한 장을 골라야 해서, 그 구간에서 실제로 가장 흔한 스택에
# 가까운 것을 쓴다. 값은 내 기록의 구간별 중앙값 (hands_db 기준).
_BUCKET_MID = {"pf": 11, "short": 20, "mid": 31, "deep": 60}


def parse_stack(stack):
    """슬롯 인자 → (슬롯키, bb, 버킷). bb 숫자도 버킷키도 받는다.

    '13' → ("13", 13, "pf") / 'pf' → ("pf", None, "pf")."""
    s = str(stack or "").strip().lower().rstrip("b")
    if s in STACK_ORDER:
        return s, None, s
    try:
        bb = int(round(float(s)))
    except ValueError:
        return None, None, None
    bucket = store._stack_bucket(bb)
    return (str(bb), bb, bucket) if bucket else (None, None, None)


def slot_label(bb, bucket):
    """슬롯 표시 이름. bb로 들어온 차트는 **넣을 때 준 숫자 그대로** 보여준다."""
    return f"{bb}bb" if bb is not None else STACK_LABEL.get(bucket, bucket)


def _slot_meta(key, rec):
    """저장된 차트 레코드 → (bb, 버킷). 구 레코드(버킷키로 저장된 것)도 읽힌다."""
    _, slot, _ = _split_key(key)
    bb = rec.get("bb")
    if bb is None and slot not in STACK_ORDER:
        try:
            bb = int(slot)
        except ValueError:
            bb = None
    bucket = rec.get("bucket") or (slot if slot in STACK_ORDER else
                                   (store._stack_bucket(bb) if bb is not None else None))
    return bb, bucket


def _pick_custom(db, pos, bucket, vs=None):
    """그 버킷에서 쓸 가져온 차트 한 장. bb 차트를 버킷 차트보다 우선한다."""
    best = None
    for key, rec in _charts(db).items():
        p, _, v = _split_key(key)
        if p != pos or v != vs or not rec.get("weights"):
            continue
        bb, bk = _slot_meta(key, rec)
        if bk != bucket:
            continue
        # bb 차트 우선, 그중에서는 이 구간에서 내가 가장 자주 노는 스택에 가까운 것
        rank = (0, abs(bb - _BUCKET_MID.get(bucket, 0))) if bb is not None else (1, 0)
        if best is None or rank < best[0]:
            best = (rank, rec, bb)
    return (best[1], best[2]) if best else (None, None)


def chart(pos, stack, db=None, vs=None):
    """(포지션, 스택) → 차트. 스택은 **bb 숫자**도 버킷키도 된다.
    db를 주면 **가져온 차트가 내장 차트를 덮어쓴다**.

    `vs`(오프너 포지션)를 주면 오픈 차트가 아니라 **그 오픈을 받았을 때의 방어 차트**다
    (BB vs BTN 등). 같은 모양(weights/jam/call)이고 빨강의 뜻만 오픈 → 3벳으로 바뀐다.
    내장 방어 차트는 없다 — 가져온 것만 있다.

    차트의 실체는 `weights` (조합 → 0~1 빈도)다. 내장 표기법 차트도 open=1.0 /
    mix=0.5로 같은 모양에 맞춰 들어오므로 아래 로직은 출처를 구분하지 않는다 —
    GTO 툴에서 가져온 혼합 빈도가 그대로 채점에 반영된다. 가져온 차트는 여기에
    `jam`(올인 빈도)을 더 들고 올 수 있는데, **채점은 여전히 합계(weights) 기준**이고
    jam은 차트를 그릴 때 레이즈/올인을 나눠 보여주는 데만 쓴다. `call`(콜·림프 빈도)은
    다르다 — 있으면 드릴이 오픈/콜/폴드 3지선다가 되고 채점도 세 몫으로 한다.

    버킷 대체는 가져온 차트와 내장 차트를 같은 사슬에서 훑는다: 예를 들어 헤즈업은
    내장 차트가 deep 한 장뿐이라, short를 가져오면 short가 그 자리를 차지한다."""
    pos = _norm_pos(pos)
    vs = _norm_vs(vs)
    kind = vs_parts(vs)[1]
    if pos not in RFI and pos not in POS_8MAX:
        return None
    # 8맥스 이름·방어 차트는 내장 차트가 없다 — 가져온 것만
    table = {} if vs else (RFI.get(pos) or {})
    slot, bb_req, bucket = parse_stack(stack)
    if not bucket:
        return None
    weights = source = jam = call = None
    bb_used = None
    exact = _charts(db).get(_ckey(pos, slot, vs)) if bb_req is not None else None
    if exact and exact.get("weights"):          # 정확히 그 bb 차트가 있으면 그걸로
        weights = {k: float(v) for k, v in exact["weights"].items()}
        jam = {k: float(v) for k, v in (exact.get("jam") or {}).items()}
        call = {k: float(v) for k, v in (exact.get("call") or {}).items()}
        source, bucket_used, bb_used = exact.get("source") or "가져온 차트", bucket, bb_req
    else:
        for b in _BUCKET_FALLBACK.get(bucket, STACK_ORDER):
            cu, cu_bb = _pick_custom(db, pos, b, vs)
            if cu:
                weights = {k: float(v) for k, v in cu["weights"].items()}
                jam = {k: float(v) for k, v in (cu.get("jam") or {}).items()}
                call = {k: float(v) for k, v in (cu.get("call") or {}).items()}
                source, bucket_used, bb_used = cu.get("source") or "가져온 차트", b, cu_bb
                break
            if b in table:
                key = (pos, b)
                if key not in _CHART_CACHE:
                    _CHART_CACHE[key] = _builtin_weights(pos, b)
                weights, bucket_used = _CHART_CACHE[key], b
                break
    if weights is None:
        return None
    pct, mix_pct = _summary(weights)
    jam_pct = (sum(min(w, weights.get(c, 0.0)) * combo_weight(c)
                   for c, w in (jam or {}).items()) / 1326 * 100)
    call_pct = (sum(min(w, weights.get(c, 0.0)) * combo_weight(c)
                    for c, w in (call or {}).items()) / 1326 * 100)
    return {
        "pos": pos, "vs": vs, "stack": slot, "chart_stack": bucket_used,
        "bucket": bucket, "bb": bb_used,
        "weights": weights, "jam": jam or {}, "call": call or {}, "source": source,
        "pct": pct, "mix_pct": mix_pct, "jam_pct": round(jam_pct, 1),
        "call_pct": round(call_pct, 1),
        # 15bb 미만은 레이즈가 아니라 푸시폴드 구간이라 묻는 액션 자체가 다르다.
        # 방어 차트는 빨강이 3벳이다 (짧은 스택이면 그 3벳이 곧 올인 — jam으로 따로 그려진다)
        # 오픈 올인을 받은 차트엔 레이즈가 없다 — 비폴드가 전부 콜이다.
        # 림프를 받은 BB의 빨강은 아이솔레이션 레이즈다
        "verb": ("콜" if kind == "allin" else "레이즈" if kind == "limp" else VS_VERB) if vs
                else ("올인" if bucket == "pf" else "오픈"),
        "allin": kind == "allin",
        "limp": kind == "limp",
        # 콜(초록)의 뜻도 갈린다: 폴드 투 히어로에선 림프, 오픈을 받았으면 그냥 콜
        "call_name": "콜" if vs else "콜(림프)",
        # 림프를 받은 BB엔 폴드가 없다 — 액션 안 하는 쪽이 체크다
        "fold_name": "체크" if kind == "limp" else "폴드",
        "label": f"{spot_name(pos, vs)} · "
                 f"{slot_label(bb_used if bb_req is None else bb_req, bucket)}",
    }


def spot_name(pos, vs=None):
    """'BB vs BTN' / 'BB vs BTN 올인' / 'BB vs SB 림프' / 'UTG' — 차트·문제·채점 문구에서 부르는 이름."""
    op, kind = vs_parts(vs)
    if not op:
        return pos
    return f"{pos} vs {op}" + ("" if kind == "raise" else " " + KIND_NAME[kind])


def verdict(pos, bucket, combo, db=None, vs=None):
    """차트가 이 조합을 어떻게 보는지: "open" / "call" / "mix" / "fold" (차트 없으면 None)."""
    c = chart(pos, bucket, db, vs)
    if not c or not combo:
        return None
    return _class(c["weights"].get(combo, 0.0), c["call"].get(combo, 0.0))


_ALL = frozenset(all_combos())


# ---------------------------------------------------------------------------
# 레인지 텍스트 가져오기 — GTO 툴에서 뽑은 문자열을 그대로 받아 차트로 삼는다
# ---------------------------------------------------------------------------

def parse_range(text):
    """레인지 텍스트 → ({조합: 빈도 0~1}, 경고 목록). 형식을 관대하게 받는다.

    섞여 있어도 되는 형태:
      ``AA, AKs, AKo``            그냥 목록 (빈도 1.0)
      ``AA:1, ATo:0.5``           빈도 (0~1)
      ``AA:100, ATo:50``          빈도 (0~100)
      ``22+, A2s+, A5s-A2s``      내장 차트와 같은 표기법 축약
      ``AhKs``                    실제 카드 2장 — 169조합으로 접는다

    0~1 스케일과 0~100 스케일은 **값 하나로는 구분되지 않는다**(``AA:1``이 100%인지
    1%인지). 그래서 토큰별이 아니라 **전체 최대값**으로 판정한다 — 최대가 1을 넘으면
    % 스케일. 이래야 ``AA:1, AKo:0.5``도, ``AA:100, AKo:50``도 옳게 읽힌다.

    구분자는 콤마·공백·줄바꿈·세미콜론 아무거나. 빈도 구분자는 ``:`` ``=`` ``@``."""
    src = re.sub(r"\s*([:=@])\s*", r"\1", text or "")
    raw, warnings = [], []
    for tok in re.split(r"[,\s;]+", src):
        if not tok:
            continue
        w, left = None, tok
        for sep in (":", "=", "@"):
            if sep in tok:
                left, right = tok.rsplit(sep, 1)
                try:
                    w = float(right.rstrip("%"))
                except ValueError:
                    left, w = tok, None
                break
        combos = _combos_of(left)
        if combos is None:
            if len(warnings) < 12:
                warnings.append(tok)
            continue
        raw.append((combos, 1.0 if w is None else w))

    if not raw:
        return {}, warnings
    # 최대값이 1을 넘으면 0~100 스케일 (위 docstring 참고)
    scale = 100.0 if max(w for _, w in raw) > 1.0 else 1.0
    out = {}
    for combos, w in raw:
        w = max(0.0, min(1.0, w / scale))
        if w <= 0:
            continue
        for c in combos:
            out[c] = max(out.get(c, 0.0), w)      # 같은 조합이 겹치면 큰 쪽
    return out, warnings


def _combos_of(tok):
    """토큰 하나 → 조합 집합 (못 읽으면 None). 카드 2장 표기도 받아준다."""
    t = tok.strip()
    if not t:
        return None
    # AhKs 처럼 실제 카드 두 장 → store._combo 로 169조합에 접는다
    if len(t) == 4 and re.fullmatch(r"[2-9TJQKAtjqka][shdcSHDC]{1}[2-9TJQKAtjqka][shdcSHDC]", t):
        c = store._combo([t[0].upper() + t[1].lower(), t[2].upper() + t[3].lower()])
        return {c} if c else None
    t = re.sub(r"[2-9tjqka]", lambda m: m.group(0).upper(), t)   # 랭크만 대문자로
    t = t.replace("S", "s").replace("O", "o")
    try:
        got = _expand_token(t)
    except (ValueError, KeyError, IndexError):
        return None
    return got if got and got <= _ALL else None


def import_chart(db, pos, stack, text, source=None, jam=None, call=None, vs=None):
    """가져온 레인지를 (포지션, 스택) 슬롯에 저장. 내장 차트를 덮어쓴다.

    `vs`를 주면 그 오프너에 대한 **방어 차트** 슬롯이다 (`BB|20|vsBTN`). 텍스트 형식은
    같고, 합계는 3벳+콜, `jam`은 그중 올인, `call`은 그중 콜이다.

    `stack`이 bb 숫자면 **그 숫자 그대로** 슬롯이 된다 (13bb와 10bb가 따로 산다).
    `jam`은 그중 올인으로 치는 빈도 — 같은 형식의 레인지 텍스트로 따로 받는다.
    채점은 합계 기준이므로 jam이 없어도 동작은 똑같다. `call`도 같은 형식으로, 합계 중
    콜(림프) 몫 — 이건 채점에 들어간다 (드릴이 3지선다가 된다)."""
    pos = _norm_pos(pos)
    if pos not in POS_8MAX:
        return {"error": f"알 수 없는 포지션: {pos} ({' / '.join(POS_8MAX)} 중 하나)"}
    vs, err = parse_vs(pos, vs)
    if err:
        return {"error": err}
    slot, bb, bucket = parse_stack(stack)
    if not slot:
        return {"error": f"알 수 없는 스택: {stack} (bb 숫자나 {'/'.join(STACK_ORDER)})"}
    weights, warnings = parse_range(text)
    if not weights:
        return {"error": "레인지를 하나도 읽지 못했습니다. 형식을 확인해 주세요.",
                "warnings": warnings}
    jam_w = {}
    if jam:
        jw, jwarn = parse_range(jam)
        warnings = warnings + jwarn
        # 올인 빈도가 합계를 넘을 수는 없다 (읽기 오차로 1~2%p 넘칠 수 있어 깎는다)
        jam_w = {k: round(min(v, weights.get(k, 0.0)), 4) for k, v in jw.items()
                 if weights.get(k, 0.0) > 0}
    call_w = {}
    kind = vs_parts(vs)[1]
    if kind == "allin":
        # 오픈 올인을 받으면 남은 액션은 콜뿐이다 — 캡처 색이 무엇이든 비폴드는 전부 콜로 본다
        # (GTO 툴이 이 콜을 빨강으로 그려도 '3벳'으로 잘못 읽히지 않게)
        jam_w, call = {}, None
        call_w = {k: round(v, 4) for k, v in weights.items()}
    if call:
        cw, cwarn = parse_range(call)
        warnings = warnings + cwarn
        # 콜 몫도 합계를 넘을 수 없고, 올인 몫과는 겹치지 않는다
        call_w = {k: round(min(v, weights.get(k, 0.0) - jam_w.get(k, 0.0)), 4)
                  for k, v in cw.items() if weights.get(k, 0.0) > jam_w.get(k, 0.0)}
    if kind == "limp":
        # 림프를 받은 BB는 레이즈냐 체크냐다 — 체크가 초록(콜 색)으로 읽혔든 파랑으로 읽혔든
        # '레이즈가 아닌 쪽'으로 접는다. 그러면 드릴은 레이즈/체크 2지선다가 된다
        weights = {k: round(w - call_w.get(k, 0.0), 4) for k, w in weights.items()}
        weights = {k: w for k, w in weights.items() if w > 0.005}
        jam_w = {k: min(v, weights[k]) for k, v in jam_w.items() if k in weights}
        call_w = {}
        if not weights:
            return {"error": "레이즈(빨강)를 하나도 읽지 못했습니다 — BB vs SB 림프 화면이 맞나요?"}
    _state_mut(db)["charts"][_ckey(pos, slot, vs)] = {
        "weights": {k: round(v, 4) for k, v in sorted(weights.items())},
        "jam": {k: jam_w[k] for k in sorted(jam_w)},
        "call": {k: call_w[k] for k in sorted(call_w)},
        "bb": bb, "bucket": bucket, "vs": vs,
        "source": (source or "").strip() or "가져온 차트",
        "ts": time.strftime("%Y-%m-%d %H:%M"),
    }
    pct, mix_pct = _summary(weights)
    return {"ok": True, "pos": pos, "vs": vs, "stack": slot, "bb": bb, "bucket": bucket,
            "label": slot_label(bb, bucket), "n": len(weights),
            "pct": pct, "mix_pct": mix_pct, "warnings": warnings}


def delete_chart(db, pos, stack, vs=None):
    """가져온 차트를 지우고 내장 차트로 되돌린다 (방어 차트는 내장이 없어 그냥 사라진다)."""
    slot, _, _ = parse_stack(stack)
    got = _state_mut(db)["charts"].pop(_ckey(_norm_pos(pos), slot, _norm_vs(vs)), None)
    return {"ok": got is not None}


def custom_slots(db):
    """가져온 차트 목록 (UI의 슬롯 표시·삭제용). 포지션 순 → 상대 순(오픈 차트 먼저) → 스택 큰 순."""
    out = []
    for key, c in _charts(db).items():
        pos, slot, vs = _split_key(key)
        bb, bucket = _slot_meta(key, c)
        w = {k: float(v) for k, v in (c.get("weights") or {}).items()}
        jam = {k: float(v) for k, v in (c.get("jam") or {}).items()}
        pct, mix_pct = _summary(w)
        jam_pct = sum(min(v, w.get(k, 0.0)) * combo_weight(k)
                      for k, v in jam.items()) / 1326 * 100
        call = {k: float(v) for k, v in (c.get("call") or {}).items()}
        call_pct = sum(min(v, w.get(k, 0.0)) * combo_weight(k)
                       for k, v in call.items()) / 1326 * 100
        out.append({"pos": pos, "vs": vs, "stack": slot, "bb": bb, "bucket": bucket,
                    "stack_label": slot_label(bb, bucket),
                    "n": len(w), "pct": pct, "mix_pct": mix_pct,
                    "jam_pct": round(jam_pct, 1), "has_jam": bool(jam),
                    "call_pct": round(call_pct, 1), "has_call": bool(call),
                    "source": c.get("source"), "ts": c.get("ts")})
    order = {p: i for i, p in enumerate(POS_ORDER)}
    out.sort(key=lambda s: (order.get(s["pos"], 99),
                            (order.get(vs_parts(s["vs"])[0], 99), _KIND_ORDER[vs_parts(s["vs"])[1]])
                            if s["vs"] else (-1, -1),
                            -(s["bb"] if s["bb"] is not None else -1)))
    return out


# ---------------------------------------------------------------------------
# 내 실전 RFI 기록 — 차트와 어긋난 조합을 찾아 출제 가중치로 쓴다
# ---------------------------------------------------------------------------

_HERO_CACHE = {"n": None, "data": None}


def personalized(db):
    """내 기록으로 가중치를 줄 수 있는 DB인지 (`--rebuild` 여부).

    `pf_faced`가 없으면 '폴드 투 히어로'를 판정할 수 없어 균등 출제로 떨어진다."""
    for r in db.get("hands", {}).values():
        return "pf_faced" in r and "stack_bb" in r
    return False


def _hero_rfi(db):
    """(체계, 포지션, 스택버킷, 조합) → [오픈 횟수, 기회 횟수].

    '기회' = pf_faced가 "none"(폴드 투 히어로), '오픈' = rfi 플래그.
    수만 핸드를 훑으므로 핸드 수를 키로 캐시한다 (임포트로만 늘어난다).

    **체계를 나눠 센다** — "8"은 가져온 8맥스 차트용(뒤에 남은 인원으로 옮긴 이름), "raw"는
    내장 차트용(핸드 기록의 원래 이름, MP1~3은 MP). 예전엔 한 키 공간에 두 이름을 다 넣었는데
    'UTG'가 두 체계에 똑같이 있어서, 4~7인 테이블의 UTG(8맥스로는 UTG1·LJ·HJ·CO)가 전부
    8맥스 UTG 차트에 섞였다 (실측: 8인 UTG 기회 6번인데 9,466번으로 집계)."""
    hands = db.get("hands", {})
    if _HERO_CACHE["n"] == len(hands) and _HERO_CACHE["data"] is not None:
        return _HERO_CACHE["data"]
    data = {}
    for r in hands.values():
        if r.get("pf_faced") != "none":
            continue
        pos = _norm_pos(r.get("hero_pos"))
        sb = store._stack_bucket(r.get("stack_bb"))
        combo = store._combo(r.get("hero_cards") or [])
        if not pos or not sb or not combo:
            continue
        for key in (("raw", pos), ("8", _pos_8max(r.get("hero_pos"), r.get("players")))):
            e = data.setdefault((*key, sb, combo), [0, 0])
            e[1] += 1
            if r.get("rfi"):
                e[0] += 1
    _HERO_CACHE.update({"n": len(hands), "data": data})
    return data


_HERO_VS_CACHE = {"n": None, "data": None}


def vs_personalized(db):
    """방어 차트에 내 기록을 겹칠 수 있는 DB인지. `pf_opener`는 방어 차트와 함께 생긴
    필드라, 그 전에 rebuild한 DB에선 없다 — 그때는 방어 쪽 겹쳐 보기·가중치만 꺼진다."""
    for r in db.get("hands", {}).values():
        return "pf_opener" in r and "stack_bb" in r
    return False


def _hero_vs(db):
    """(내 자리, 스택버킷, 오프너 자리, 조합) → [방어 횟수, 기회 횟수, 그중 3벳(올인 포함)].

    '기회' = 오픈 한 번만 받은 핸드(`pf_opener`가 있다 — 림프·콜러·3벳 팟은 빠져 있다).
    '방어' = 폴드 말고 다 (콜·3벳·올인). 두 자리 모두 8맥스 이름으로 옮겨 센다 —
    방어 차트는 가져온 8맥스 차트뿐이라서다."""
    hands = db.get("hands", {})
    if _HERO_VS_CACHE["n"] == len(hands) and _HERO_VS_CACHE["data"] is not None:
        return _HERO_VS_CACHE["data"]
    data = {}
    for r in hands.values():
        if r.get("pf_limper") and r.get("pf_faced") == "limp":
            # SB 혼자 림프한 팟의 BB — '방어' 대신 아이솔레이션 레이즈를 센다 (체크가 나머지)
            pos = _pos_8max(r.get("hero_pos"), r.get("players"))
            sb = store._stack_bucket(r.get("stack_bb"))
            combo = store._combo(r.get("hero_cards") or [])
            if pos and sb and combo:
                e = data.setdefault((pos, sb, _pos_8max(r.get("pf_limper"), r.get("players")) + LIMP,
                                     combo), [0, 0, 0])
                e[1] += 1
                if r.get("pf_action") in ("open", "3bet", "allin"):
                    e[0] += 1
                    e[2] += 1
            continue
        if not r.get("pf_opener") or r.get("pf_faced") != "raise":
            continue
        n = r.get("players")
        # _pos_8max는 MP1/MP2 같은 원래 이름을 받아야 몇 번째 자리인지 센다 (_norm_pos 전에)
        pos = _pos_8max(r.get("hero_pos"), n)
        vs = _pos_8max(r.get("pf_opener"), n)
        # 오픈 올인을 받은 핸드는 따로 센다 (rebuild 전 DB엔 이 필드가 없어 전부 레이즈 쪽에 섞인다)
        if vs and r.get("pf_opener_allin"):
            vs += ALLIN
        sb = store._stack_bucket(r.get("stack_bb"))
        combo = store._combo(r.get("hero_cards") or [])
        if not pos or not vs or not sb or not combo:
            continue
        e = data.setdefault((pos, sb, vs, combo), [0, 0, 0])
        e[1] += 1
        act = r.get("pf_action")
        if act != "fold":
            e[0] += 1
        if act in ("3bet", "allin"):
            e[2] += 1
    _HERO_VS_CACHE.update({"n": len(hands), "data": data})
    return data


def hero_cells(db, pos, bucket, vs=None, builtin=False):
    """그 스팟의 조합별 실전 기록 {조합: [액션 횟수, 기회 횟수, (방어면) 3벳 횟수]}.
    오픈 차트면 액션 = 오픈, 방어 차트면 액션 = 방어(콜+3벳). rebuild 안 된 DB면 빈 dict.

    `builtin`은 비교 대상이 내장 근사 차트일 때 — 그때만 원래 자리 이름으로 센 기록을 쓴다
    (가져온 차트는 8맥스 체계라 뒤에 남은 인원으로 옮긴 기록이어야 한다). 차트의 `source`가
    없으면 내장이다."""
    pos = _norm_pos(pos)
    if vs:
        if not vs_personalized(db):
            return {}
        vs = _norm_vs(vs)
        return {k[3]: e for k, e in _hero_vs(db).items()
                if k[0] == pos and k[1] == bucket and k[2] == vs}
    if not personalized(db):
        return {}
    scheme = "raw" if builtin else "8"
    return {k[3]: e for k, e in _hero_rfi(db).items()
            if k[0] == scheme and k[1] == pos and k[2] == bucket}


def hero_record(db, pos, bucket, combo, vs=None, builtin=False):
    """그 조합을 실제로 어떻게 쳤는지 (기회 없으면 None)."""
    e = hero_cells(db, pos, bucket, vs, builtin).get(combo)
    if not e or not e[1]:
        return None
    out = {"opens": e[0], "opps": e[1], "rate": round(e[0] / e[1] * 100)}
    if vs:
        out["raises"] = e[2]
    return out


# ---------------------------------------------------------------------------
# 출제
# ---------------------------------------------------------------------------

def _vs_list(db, pos):
    """그 포지션에 가져온 방어 차트의 상대(오프너) 목록 — 행동 순서대로."""
    got = {vs for k in _charts(db) for p, _, vs in [_split_key(k)] if p == pos and vs}
    key = lambda v: (POS_8MAX.index(vs_parts(v)[0]) if vs_parts(v)[0] in POS_8MAX else 99,
                     _KIND_ORDER[vs_parts(v)[1]])
    return sorted(got, key=key)


def seats_off(max_seats=8):
    """그 인원 테이블에 없는 8맥스 자리 — 인원이 줄면 **앞자리부터** 빠진다 (7맥스면 UTG,
    6맥스면 UTG·UTG1 …). 뒤에 남은 인원이 같으면 같은 자리라 나머지 차트는 그대로 쓴다."""
    try:
        n = min(8, max(3, int(max_seats)))
    except (TypeError, ValueError):
        n = 8
    return set(POS_8MAX[:8 - n])


def max_name(n):
    """6명 이상은 'N맥스'(테이블 포맷), 5명 이하는 'N명'(파이널 테이블 등) — 프론트와 같은 규칙."""
    return f"{n}맥스" if n >= 6 else f"{n}명"


def _contexts(db, positions=None, stacks=None, max_seats=8):
    """출제 대상 (포지션, 스택버킷, 상대, 몫). 빈 필터 = 전체.

    오픈 차트는 (포지션, 버킷)마다 하나, 방어 차트는 가져온 상대마다 하나씩이다. 방어
    차트가 상대 7명분 들어와도 그 포지션이 7배로 쏠리지 않게, 한 (포지션, 버킷)의 방어
    문제들은 몫(share)을 나눠 가져 **합쳐서 오픈 차트 하나만큼**만 나오게 한다.

    `max_seats`(테이블 인원)에서 빠지는 앞자리는 자리로도, 오프너로도 출제하지 않는다 —
    7맥스만 치는 사람에게 8맥스 UTG 문제는 실전에 없는 자리다."""
    positions = set(positions or ()) or set(POS_ORDER)
    stacks = set(stacks or ()) or set(STACK_ORDER)
    off = seats_off(max_seats)
    # 내장 'MP'는 LJ·HJ를 하나로 묶은 옛 근사 차트다 — 그 자리를 가져온 8맥스 차트가 있으면 중복이고,
    # 4명 이하 테이블엔 MP 자리 자체가 없다
    imported = {_split_key(k)[0] for k in _charts(db) if not _split_key(k)[2]}
    drop_mp = bool(imported & {"LJ", "HJ"}) or len(off) >= 4
    out = []
    for p in POS_ORDER:
        if p not in positions or p in off or (p == "MP" and drop_mp):
            continue
        vss = [v for v in _vs_list(db, p) if vs_parts(v)[0] not in off]
        for s in STACK_ORDER:
            if s not in stacks:
                continue
            out.append((p, s, None, 1.0))
            out.extend((p, s, vs, 1.0 / len(vss)) for vs in vss)
    return out


def _recent_combos(db):
    return {(a.get("pos"), a.get("stack"), a.get("vs"), a.get("combo"))
            for a in _state(db)["attempts"][-RECENT_SKIP:]}


_SUITS = "shdc"


def _deal(combo):
    """조합 라벨 → 실제 카드 두 장 (수딧/오프수딧에 맞는 무늬를 무작위로)."""
    if len(combo) == 2:
        s1, s2 = random.sample(_SUITS, 2)
        return [combo[0] + s1, combo[1] + s2]
    hi, lo, suited = combo[0], combo[1], combo[2] == "s"
    if suited:
        s = random.choice(_SUITS)
        return [hi + s, lo + s]
    s1, s2 = random.sample(_SUITS, 2)
    return [hi + s1, lo + s2]


def next_question(db, positions=None, stacks=None, max_seats=8):
    """다음 오픈 레인지 문제. AI 호출 없음 — 전부 로컬에서 만든다.

    가중치: 기본 1. 내 실전 기록이 차트와 어긋날수록 크게 (최대 ×9), 이미 차트대로
    잘 치고 있는 조합은 작게 (×0.4) — 아는 걸 계속 묻지 않기 위해서다.
    최근에 나온 조합은 ×0.15로 눌러 같은 문제가 연달아 나오는 걸 막는다."""
    ctxs = _contexts(db, positions, stacks, max_seats)
    if not ctxs:
        return {"error": "선택한 조합에 해당하는 차트가 없습니다."}
    recent = _recent_combos(db)

    pool, weights = [], []
    for pos, bucket, vs, share in ctxs:
        c = chart(pos, bucket, db, vs)
        if not c:
            continue
        hero = hero_cells(db, pos, bucket, vs, builtin=not c["source"])
        for combo in all_combos():
            # 차트 빈도가 곧 목표치다 — 가져온 차트의 혼합 빈도(0.62 등)도 그대로 쓴다.
            # 오픈 차트: 실전 기록(rfi)은 레이즈만 세므로 콜(림프) 몫은 빼고 비교한다.
            # 방어 차트: 실전 기록이 방어(콜+3벳) 전체라 합계 그대로 비교한다
            target = c["weights"].get(combo, 0.0)
            if not vs:
                target -= c["call"].get(combo, 0.0)
            w = 1.0
            rec = hero.get(combo)
            if rec and rec[1]:
                dev = abs(rec[0] / rec[1] - target)
                conf = min(1.0, rec[1] / 4)      # 표본 4회면 최대 신뢰
                w = 1.0 + 8.0 * dev * conf if dev > 0.25 else 0.4
            if (pos, bucket, vs, combo) in recent:
                w *= 0.15
            pool.append((pos, bucket, vs, combo))
            weights.append(w * share)
    if not pool:
        return {"error": "출제할 차트가 없습니다."}

    pos, bucket, vs, combo = random.choices(pool, weights=weights)[0]
    c = chart(pos, bucket, db, vs)
    verb = c["verb"]
    op, kind = vs_parts(vs)
    facing_allin = kind == "allin"
    if kind == "limp":
        prompt = f"{op} 림프 — 나에게 왔습니다. "
        raise_label = "레이즈(아이솔)"
    elif vs:
        prompt = f"{op} {KIND_NAME[kind]} — 나머지는 폴드하고 나에게 왔습니다. "
        raise_label = verb + ("(올인)" if bucket == "pf" else "")
    else:
        prompt = ("헤즈업, 상대 BB. " if pos == "SB(BTN)"
                  else "앞이 전부 폴드하고 나에게 왔습니다. ")
        raise_label = verb + ("(푸시)" if verb == "올인" else "(레이즈)")
    return {"question": {
        "pos": pos, "vs": vs, "stack": bucket, "combo": combo,
        "cards": _deal(combo),
        # 인원이 준 테이블의 첫 자리는 그 포맷에서 'UTG'라 부른다 (7맥스 UTG = 8맥스 UTG1)
        "pos_label": POS_KO.get(pos, pos) + (
            f" · {max_name(int(max_seats))} UTG" if seats_off(max_seats)
            and pos == POS_8MAX[len(seats_off(max_seats))] else ""),
        "vs_label": f"vs {op} {KIND_NAME[kind]}" if vs else None,
        "stack_label": STACK_LABEL.get(bucket, "?"),
        "verb": verb,
        "chart_source": c.get("source"),
        "prompt": prompt,
        # 콜(초록)이 있는 차트만 3지선다 — 없는 차트에 콜 버튼을 띄우면 정답이 없는 선택지가 된다
        # 오픈 올인을 받으면 콜/폴드 둘뿐이다 (레이즈 선택지가 없다)
        "choices": [
            *([] if facing_allin else [{"id": "open", "label": raise_label}]),
            *([{"id": "call", "label": c["call_name"]}] if c["call"] else []),
            {"id": "fold", "label": c["fold_name"]},
        ],
    }}


# ---------------------------------------------------------------------------
# 채점 — 전부 로컬. 차트가 정답이므로 AI가 필요 없다.
# ---------------------------------------------------------------------------

GRADE_OK, GRADE_MIX, GRADE_BAD = "좋음", "무난", "실수"


def grade(db, pos, bucket, combo, choice, record=True, vs=None):
    """고른 액션을 차트와 대조해 채점하고 응시 기록을 남긴다. `vs`면 방어 차트로 채점."""
    c = chart(pos, bucket, db, vs)
    if not c:
        return {"error": "해당 포지션·스택 차트가 없습니다."}
    if combo not in _ALL:
        return {"error": f"알 수 없는 조합: {combo}"}
    freq = c["weights"].get(combo, 0.0)
    call_f = min(c["call"].get(combo, 0.0), freq)
    v, verb, fold = _class(freq, call_f), c["verb"], c["fold_name"]
    if c["call"]:
        return _grade3(db, c, pos, bucket, combo, choice, freq, call_f, v, record)
    # 0/1이 아닌 빈도는 그 자체가 정보다 — 가져온 차트에서만 나온다
    fs = f" (차트 빈도 {freq * 100:.0f}% {verb})" if 0.0 < freq < 1.0 else ""
    if v == "mix":
        g = GRADE_MIX
        head = f"{combo}는 이 구간의 **경계 핸드**입니다{fs} — {verb}도 {fold}도 됩니다."
    elif choice == v:
        g = GRADE_OK
        head = (f"{combo}는 차트상 **{verb}** 구간입니다{fs}." if v == "open"
                else f"{combo}는 차트상 **{fold}** 구간입니다{fs}.")
    else:
        g = GRADE_BAD
        head = (f"{combo}는 차트상 **{verb}** 구간인데 {fold}했습니다{fs}." if v == "open"
                else f"{combo}는 차트상 **{fold}** 구간인데 {verb}했습니다{fs}.")

    lines = [head,
             f"{spot_name(c['pos'], c['vs'])} · {STACK_LABEL.get(bucket, '?')} {verb} 레인지는 상위 "
             f"**{c['pct']}%** (경계 {c['mix_pct']}% 포함)."]
    return _grade_tail(db, c, pos, bucket, combo, choice, g, v, freq, lines, record)


def _grade3(db, c, pos, bucket, combo, choice, freq, call_f, v, record):
    """콜(림프)이 있는 차트의 3지선다 채점. 고른 액션의 차트 빈도로 판정한다:
    75% 이상 [좋음] / 25% 초과 [무난] / 그 이하 [실수] (2지선다 규칙을 셋으로 늘린 것)."""
    verb = c["verb"]
    d = _dist(freq, call_f)
    name = {"open": verb, "call": c["call_name"], "fold": c["fold_name"]}
    p = d.get(choice, 0.0)
    g = GRADE_OK if p >= OPEN_HI else (GRADE_MIX if p > FOLD_LO else GRADE_BAD)
    mix = " · ".join(f"{name[k]} {d[k] * 100:.0f}%" for k in ("open", "call", "fold")
                     if d[k] > 0.005)
    best = max(d, key=d.get)
    if g == GRADE_OK:
        head = f"{combo}는 차트상 **{name[best]}** 구간입니다 ({mix})."
    elif g == GRADE_MIX:
        head = (f"{combo}는 **혼합** 구간입니다 ({mix}) — "
                f"{name.get(choice, choice)}도 됩니다.")
    else:
        head = (f"{combo}는 차트상 **{name[best]}** 쪽입니다 ({mix}) — "
                f"{name.get(choice, choice)} 빈도는 {p * 100:.0f}%뿐입니다.")
    lines = [head,
             f"{spot_name(c['pos'], c['vs'])} · {STACK_LABEL.get(bucket, '?')} 차트: 액션 합계 **{c['pct']}%** "
             f"(그중 콜 {c['call_pct']}%, 경계 {c['mix_pct']}% 포함)."]
    return _grade_tail(db, c, pos, bucket, combo, choice, g, v, freq, lines, record)


def _grade_tail(db, c, pos, bucket, combo, choice, g, v, freq, lines, record):
    """채점 공통 뒷부분 — 출처·실전 기록 문구, 응시 기록, 응답."""
    verb = c["verb"]
    if c.get("source"):
        lines.append(f"차트 출처: **{c['source']}**"
                     + ("" if c["chart_stack"] == bucket
                        else f" ({STACK_LABEL.get(c['chart_stack'])} 차트로 대체)"))
    vs = c["vs"]
    rec = hero_record(db, pos, bucket, combo, vs, builtin=not c.get("source"))
    if rec and vs and (c["allin"] or c["limp"]):
        lines.append(f"실전 기록: 이 스팟에서 {combo} {rec['opps']}회 중 "
                     f"{rec['opens']}회 {verb} (**{rec['rate']}%**).")
    elif rec and vs:
        lines.append(f"실전 기록: 이 스팟에서 {combo} {rec['opps']}회 중 "
                     f"{rec['opens']}회 방어 (**{rec['rate']}%** · 그중 {verb} {rec['raises']}회).")
    elif rec:
        lines.append(f"실전 기록: 이 스팟에서 {combo} {rec['opps']}회 중 "
                     f"{rec['opens']}회 {verb} (**{rec['rate']}%**).")
    if record:
        record_attempt(db, pos, bucket, combo, choice, g, vs)
    return {"grade": g, "correct": v, "verb": verb, "call_name": c["call_name"],
            "fold_name": c["fold_name"],
            "vs": vs, "freq": round(freq, 3),
            "call": round(min(c["call"].get(combo, 0.0), freq), 3),
            "text": "\n".join(lines), "hero": rec, "source": c.get("source"),
            "pct": c["pct"], "mix_pct": c["mix_pct"]}


# ---------------------------------------------------------------------------
# 차트 보기 (13×13 그리드)
# ---------------------------------------------------------------------------

def chart_view(db, pos, stack, vs=None):
    """그리드용 셀 맵. 내 실전 오픈 비율을 같이 실어 차트와 겹쳐 볼 수 있게 한다.

    셀의 `w`는 액션 합계, `jam`은 그중 올인 몫, `call`은 콜(림프) 몫이다
    (레이즈 몫 = w - jam - call). 방어 차트면 실전 비율은 **방어(콜+3벳) 비율**이다.
    `dev`는 실전 기록이 차트와 어긋난 칸 — 판정 규칙을 프론트 두 곳에 두지 않으려고 여기서 정한다."""
    c = chart(pos, stack, db, vs)
    if not c:
        return {"error": "해당 포지션·스택 차트가 없습니다."}
    # 실전 기록은 버킷 단위로만 쌓인다 (핸드마다 스택이 제각각이라 bb로는 안 묶인다)
    hero = hero_cells(db, pos, c["bucket"], c["vs"], builtin=not c["source"])
    acts = ("open", "call") if c["vs"] and not c["limp"] else ("open",)
    cells = {}
    for combo in all_combos():
        w = c["weights"].get(combo, 0.0)
        cl = min(c["call"].get(combo, 0.0), w)
        cell = {"v": _class(w, cl), "w": round(w, 3)}
        j = min(c["jam"].get(combo, 0.0), w)
        if j > 0:
            cell["jam"] = round(j, 3)
        if cl > 0:
            cell["call"] = round(cl, 3)
        e = hero.get(combo)
        if e and e[1]:
            rate = round(e[0] / e[1] * 100)
            cell.update({"opens": e[0], "opps": e[1], "rate": rate,
                         "dev": (cell["v"] in acts and rate < 50) or
                                (cell["v"] == "fold" and rate > 25)})
            if c["vs"]:
                cell["raises"] = e[2]
        cells[combo] = cell
    return {"pos": c["pos"], "vs": c["vs"], "allin": c["allin"], "limp": c["limp"],
            "fold_name": c["fold_name"], "call_name": c["call_name"],
            "stack": c["stack"], "chart_stack": c["chart_stack"],
            "bucket": c["bucket"], "bb": c["bb"],
            "label": c["label"], "verb": c["verb"], "source": c.get("source"),
            "pct": c["pct"], "mix_pct": c["mix_pct"], "jam_pct": c["jam_pct"],
            "has_jam": bool(c["jam"]), "call_pct": c["call_pct"],
            "has_call": bool(c["call"]), "cells": cells}


# ---------------------------------------------------------------------------
# 응시 기록 · 성적표
# ---------------------------------------------------------------------------

def _state(db):
    """읽기 전용 — **DB를 건드리지 않는다** (클라우드 푸시 중 최상위 키가 늘면
    업로드가 통째로 실패한다. quiz._state와 같은 이유)."""
    q = db.get("ranges") or {}
    return {"attempts": q.get("attempts") or []}


def _state_mut(db):
    q = db.setdefault("ranges", {})
    q.setdefault("attempts", [])
    q.setdefault("charts", {})          # 가져온 차트: "POS|bucket" → {weights, source, ts}
    return q


def record_attempt(db, pos, bucket, combo, choice, g, vs=None):
    q = _state_mut(db)
    a = {"ts": time.strftime("%Y-%m-%d %H:%M"),
         "pos": pos, "stack": bucket, "combo": combo, "choice": choice, "grade": g}
    if vs:
        a["vs"] = vs
    q["attempts"].append(a)
    if len(q["attempts"]) > MAX_ATTEMPTS:
        del q["attempts"][:len(q["attempts"]) - MAX_ATTEMPTS]


def scoreboard(db):
    attempts = _state(db)["attempts"]
    grades = {GRADE_OK: 0, GRADE_MIX: 0, GRADE_BAD: 0}
    by_pos = {}
    for a in attempts:
        if a.get("grade") in grades:
            grades[a["grade"]] += 1
        p = a.get("pos") or "?"
        s = by_pos.setdefault(p, {"pos": p, "n": 0, "ok": 0})
        s["n"] += 1
        if a.get("grade") in (GRADE_OK, GRADE_MIX):
            s["ok"] += 1
    n = sum(grades.values())
    return {
        "total": len(attempts),
        "grades": grades,
        # 경계 핸드는 어느 쪽을 골라도 무난이므로 정답률 분자에 넣는다
        "ok_rate": round((grades[GRADE_OK] + grades[GRADE_MIX]) / n * 100) if n else None,
        "by_pos": sorted(by_pos.values(),
                         key=lambda s: POS_ORDER.index(s["pos"])
                         if s["pos"] in POS_ORDER else 9),
        "recent": attempts[-12:][::-1],
    }


def state_view(db):
    """UI 초기 상태 — 토글 선택지와 성적표."""
    custom = custom_slots(db)
    have = set(RFI) | {s["pos"] for s in custom}
    if {s["pos"] for s in custom if not s["vs"]} & {"LJ", "HJ"}:
        have.discard("MP")          # 가져온 LJ·HJ가 있으면 옛 내장 MP는 출제하지 않는다 (_contexts와 같은 규칙)
    return {
        # n=None → 프론트 토글이 개수 배지/흐림 처리를 하지 않는다 (차트는 항상 있다)
        "positions": [{"key": p, "label": p, "n": None} for p in POS_ORDER if p in have],
        # 가져오기 패널의 포지션 선택지 (grab_chart와 같은 8맥스 이름)
        "import_positions": [{"key": p, "label": POS_KO[p], "n": None} for p in POS_8MAX],
        # 방어 차트의 상대(오프너) 선택지 — BB는 오프너가 될 수 없다. 오픈 레이즈/오픈 올인을 따로
        "import_vs": [{"key": p + tail, "label": f"vs {p} {name}", "n": None}
                      for p in POS_8MAX[:-1]
                      for tail, name in (("", "오픈"), (ALLIN, "올인"))]
                     + [{"key": "SB" + LIMP, "label": "vs SB 림프", "n": None}],
        "stacks": [{"key": s, "label": STACK_LABEL[s], "n": None} for s in STACK_ORDER],
        "personalized": personalized(db),
        # 방어 차트가 있는데 이게 False면 '--rebuild 하면 방어 기록도 겹쳐진다' 안내를 띄운다
        "vs_personalized": vs_personalized(db),
        "custom": custom,
        "scoreboard": scoreboard(db),
    }


if __name__ == "__main__":                        # 차트 점검용 (python3 ranges.py)
    print(f"{'포지션':<9}{'스택':>9}  {'오픈%':>7} {'경계%':>7}  {'폴드칸':>6}")
    for p in RFI:
        for b in STACK_ORDER:
            c = chart(p, b)
            bad = set(c["weights"]) - _ALL
            assert not bad, f"{p}/{b}: 알 수 없는 조합 {bad}"
            # 전부 open/mix면 [실수]가 나올 수 없는 차트다 — 드릴로서 무의미
            folds = sum(1 for x in _ALL if _class(c["weights"].get(x, 0.0)) == "fold")  # 내장엔 콜 없음
            assert folds, f"{p}/{b}: 폴드 구간이 없다"
            fb = "" if c["chart_stack"] == b else f"  ←{c['chart_stack']}"
            print(f"{p:<9}{STACK_LABEL[b]:>9}  {c['pct']:>6.1f}% "
                  f"{c['mix_pct']:>6.1f}%  {folds:>5}칸{fb}")
