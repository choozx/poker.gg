#!/usr/bin/env python3
"""🎯 문제 풀기 — 내가 자주 실수하는 스팟을 골라 문제로 출제하는 도메인 모듈.

핵심 아이디어: **출제는 로컬(공짜), 채점만 AI**.
DB에 이미 쌓인 내 핸드를 히어로의 결정 지점에서 잘라 보여주고 액션을 고르게 한 뒤,
그 선택을 AI가 평가한다. 정답을 '실제로 내가 한 액션'으로 두지 않는 게 중요하다 —
애초에 그 핸드가 출제된 이유가 그 액션이 의심스러워서이기 때문이다.

스팟(=드릴 단위)을 고르는 축은 두 가지:

1. **휴리스틱 리크** (`_heuristic_spots`) — 임포트 시 frozen된 `review` 필드
   (큰 손실 / 쇼다운 패배 / 올인 패배)를 포지션별로 묶는다. 구 DB에도 있는 필드라
   `--rebuild` 없이 항상 동작한다.
2. **통계 이탈** (`_freq_spots`) — 포지션×스택버킷별 RFI 빈도와 '레이즈에 직면했을 때
   참여율'을 기준선과 비교해 벗어난 구간을 찾는다. `pf_faced`/`stack_bb`/`rfi`/`pf_action`
   이 필요하므로 **구 DB(미rebuild)에서는 자동으로 비활성**된다 (`freq_available`).

기준선(`RFI_BASE`/`VS_RAISE_BASE`)은 솔버 출력이 아니라 대략적인 MTT 참고값이다.
이 값들의 역할은 '어느 스팟을 물어볼지' 고르는 것뿐이고, 실제 판정은 AI가 하므로
기준선이 조금 어긋나도 최악의 경우 멀쩡한 스팟이 출제되고 AI가 [좋음]을 줄 뿐이다.

의존 방향: convert ← store ← quiz ← gui (bankroll.py와 같은 위상)
"""

import random
import time

import convert
import store

# ---------------------------------------------------------------------------
# 기준선 — 대략적인 MTT 참고값 (솔버 출력 아님). 스팟 선정용이지 판정용이 아니다.
# ---------------------------------------------------------------------------

# 폴드 투 히어로 상황에서 오픈해야 하는 대략적 비율 (%)
RFI_BASE = {"UTG": 16, "MP": 19, "CO": 27, "BTN": 44, "SB": 40, "SB(BTN)": 80}
# 앞에서 레이즈가 들어왔을 때 계속 가는(콜+3벳) 대략적 비율 (%)
# (BB는 앤티가 있는 토너에서 팟오즈 때문에 아주 넓게 디펜스한다 — 기준을 높게 잡는다)
VS_RAISE_BASE = {"UTG": 17, "MP": 20, "CO": 24, "BTN": 28, "SB": 25, "BB": 50,
                 "SB(BTN)": 45}
# 스택이 얕을수록 오픈은 넓어진다 (푸시폴드 구간)
RFI_STACK_MULT = {"pf": 1.15, "short": 1.05, "mid": 1.0, "deep": 0.95}

MIN_N_FREQ = 40       # 통계 이탈 판정 최소 표본
MIN_DEV = 8.0         # 기준선 대비 몇 %p 벗어나야 리크로 볼지
MIN_N_HEURISTIC = 8   # 휴리스틱 스팟 최소 핸드 수

STACK_LABEL = {"pf": "<15bb", "short": "15–25bb", "mid": "25–40bb", "deep": "40bb+",
               None: "스택 미상"}

MAX_ATTEMPTS = 500    # DB에 남기는 최근 응시 기록 수 (DB는 클라우드 동기화 대상 — 작게 유지)
MAX_CACHE = 400       # 채점 결과 캐시 항목 수

# 출제 탐색 비용: 후보 핸드 1개당 parse_hand 1회. 최악 4×25 = 100회 ≈ 수백 ms이고,
# 필터가 좁을 때만 끝까지 간다. 늘리면 AI 생성 폴백은 줄지만 응답이 느려진다.
MAX_SPOT_TRIES = 4    # 한 스팟이 실패하면 다음 순위 스팟까지 시도할 횟수
SCAN_PER_SPOT = 25    # 스팟당 훑어볼 미출제 핸드 수


def _norm_pos(pos):
    """MP1/MP2/MP3 → MP 로 묶는다 (기준선 테이블 조회용)."""
    if not pos:
        return "?"
    if pos.startswith("MP"):
        return "MP"
    return pos


def _state(db):
    """읽기 전용 접근 — **DB를 절대 건드리지 않는다.**

    클라우드 푸시 스레드가 DB 전체를 json.dumps 하는 동안 여기서 최상위 키를 하나라도
    추가하면 'dictionary changed size during iteration'으로 업로드가 통째로 실패한다.
    조회 경로(spots_view/scoreboard/cache_get)는 반드시 이걸 쓸 것."""
    q = db.get("quiz") or {}
    return {"attempts": q.get("attempts") or [], "cache": q.get("cache") or {}}


def _state_mut(db):
    """쓰기용 — 없으면 만든다. 실제로 기록을 남기는 경로에서만 호출할 것."""
    q = db.setdefault("quiz", {})
    q.setdefault("attempts", [])
    q.setdefault("cache", {})
    return q


# ---------------------------------------------------------------------------
# 스팟 탐지
# ---------------------------------------------------------------------------

def freq_available(db):
    """통계 이탈 축을 쓸 수 있는 DB인지 (rebuild 여부 판정).

    불리언 필드는 False와 '없음'이 구분되지 않으므로 키 자체의 존재로 본다.
    `pf_faced`는 이 기능과 함께 추가된 필드라, 이게 있으면 최신 rebuild가 된 DB다."""
    for r in db.get("hands", {}).values():
        return "pf_faced" in r and "stack_bb" in r
    return False


def _heuristic_spots(db):
    """frozen된 review 사유 × 포지션 (× 스택버킷) 으로 묶은 리크 스팟."""
    buckets = {}
    has_stack = freq_available(db)
    for hid, r in db["hands"].items():
        for reason in (r.get("review") or []):
            # "큰 손실 -23.4bb" → "큰 손실" (수치는 핸드마다 달라 묶이지 않는다)
            kind = "큰 손실" if reason.startswith("큰 손실") else reason
            pos = r.get("hero_pos") or "?"
            sb = store._stack_bucket(r.get("stack_bb")) if has_stack else None
            key = f"h|{kind}|{pos}|{sb or '-'}"
            b = buckets.setdefault(key, {
                "key": key, "kind": "heuristic", "reason": kind, "pos": pos,
                "stack": sb, "hands": [],
            })
            b["hands"].append(hid)
    out = []
    for b in buckets.values():
        if len(b["hands"]) < MIN_N_HEURISTIC:
            continue
        stack_s = f" · {STACK_LABEL[b['stack']]}" if b["stack"] else ""
        b["n"] = len(b["hands"])
        b["label"] = f"{b['pos']}{stack_s} · {b['reason']}"
        b["detail"] = f"{b['n']}핸드"
        # 표본이 클수록 자주 반복되는 리크 — 로그 스케일로 완만하게
        b["score"] = min(100.0, b["n"] ** 0.6)
        out.append(b)
    return out


def _freq_spots(db):
    """포지션×스택버킷별 빈도가 기준선에서 벗어난 스팟 (rebuild된 DB 전용)."""
    rfi = {}       # key -> [made, opp, [hand_id...]]
    vs = {}        # key -> [continued, faced, [hand_id...]]
    for hid, r in db["hands"].items():
        pos = r.get("hero_pos") or "?"
        sb = store._stack_bucket(r.get("stack_bb"))
        if sb is None:
            continue
        faced = r.get("pf_faced")
        if faced == "none":                       # 폴드 투 히어로 = 오픈 기회
            e = rfi.setdefault((pos, sb), [0, 0, []])
            e[1] += 1
            e[2].append(hid)
            if r.get("rfi"):
                e[0] += 1
        elif faced == "raise":                    # 앞에서 레이즈가 들어온 상황
            # 폴드도 반드시 분모에 들어가야 한다 — 빠지면 참여율이 100%에 붙는다
            e = vs.setdefault((pos, sb), [0, 0, []])
            e[1] += 1
            e[2].append(hid)
            if r.get("pf_action") in ("call", "3bet", "allin"):
                e[0] += 1
        # faced in (None, "limp") — 워크/림프 상황은 두 축 어디에도 넣지 않는다

    out = []
    for (pos, sb), (made, opp, hids) in rfi.items():
        base = RFI_BASE.get(_norm_pos(pos))
        if base is None or opp < MIN_N_FREQ:
            continue
        base *= RFI_STACK_MULT.get(sb, 1.0)
        actual = made / opp * 100
        dev = actual - base
        if abs(dev) < MIN_DEV:
            continue
        out.append({
            "key": f"f|rfi|{pos}|{sb}", "kind": "freq", "pos": pos, "stack": sb,
            "reason": "오픈 과다" if dev > 0 else "오픈 과소",
            "label": f"{pos} · {STACK_LABEL[sb]} · 오픈 {'과다' if dev > 0 else '과소'}",
            "detail": f"RFI {actual:.0f}% (기준 {base:.0f}%) · {opp}회 기회",
            "n": opp, "hands": hids,
            "score": min(100.0, abs(dev) * min(1.0, opp / 200) * 4),
        })
    for (pos, sb), (cont, faced, hids) in vs.items():
        base = VS_RAISE_BASE.get(_norm_pos(pos))
        if base is None or faced < MIN_N_FREQ:
            continue
        actual = cont / faced * 100
        dev = actual - base
        if abs(dev) < MIN_DEV:
            continue
        out.append({
            "key": f"f|vs|{pos}|{sb}", "kind": "freq", "pos": pos, "stack": sb,
            "reason": "콜 과다" if dev > 0 else "폴드 과다",
            "label": f"{pos} · {STACK_LABEL[sb]} · 레이즈 상대 {'참여 과다' if dev > 0 else '참여 과소'}",
            "detail": f"참여율 {actual:.0f}% (기준 {base:.0f}%) · {faced}회 직면",
            "n": faced, "hands": hids,
            "score": min(100.0, abs(dev) * min(1.0, faced / 200) * 4),
        })
    return out


def _spot_matches(spot, positions, stacks):
    """포지션/스택 필터에 걸리는 스팟인지. 빈 필터 = 전체 허용.

    포지션은 `_norm_pos`로 비교하므로 토글 'MP' 하나가 MP1/MP2/MP3를 모두 잡는다.
    스택 필터가 켜져 있는데 스팟에 스택 정보가 없으면(미rebuild DB의 휴리스틱 스팟)
    걸러낸다 — 어느 구간인지 알 수 없는 걸 특정 구간으로 셀 수는 없다."""
    if positions and _norm_pos(spot.get("pos")) not in positions:
        return False
    if stacks and spot.get("stack") not in stacks:
        return False
    return True


def leak_spots(db, positions=None, stacks=None, streets=None):
    """두 축을 번갈아 섞은 리크 스팟 목록 (포지션/스택/스트릿 필터 적용).

    두 축의 점수는 단위가 달라(빈도 이탈 %p vs 핸드 수) 그냥 합쳐 정렬하면 한쪽이
    상위를 독식한다. 각 축을 따로 정렬한 뒤 번갈아 꺼내 양쪽이 모두 출제되게 한다.

    스트릿은 스팟의 속성이 아니라 핸드 안 '결정 지점'의 속성이라 여기서는 걸러낼 게
    하나뿐이다: 통계 이탈 스팟은 RFI·디펜스 빈도라는 **프리플랍 지표**라서, 프리플랍을
    뺀 스트릿 필터에서는 출제 대상이 되면 안 된다 (턴 문제에 '오픈 과다' 딱지가 붙는다).
    나머지 스트릿 필터링은 `_pick_decision`이 결정 지점 단위로 처리한다."""
    positions = set(positions or ())
    stacks = set(stacks or ())
    streets = set(streets or ())
    keep = lambda ss: [s for s in ss if _spot_matches(s, positions, stacks)]
    heur = sorted(keep(_heuristic_spots(db)), key=lambda s: -s["score"])
    use_freq = freq_available(db) and (not streets or "preflop" in streets)
    freq = sorted(keep(_freq_spots(db)), key=lambda s: -s["score"]) if use_freq else []
    out = []
    for i in range(max(len(heur), len(freq))):
        if i < len(freq):
            out.append(freq[i])
        if i < len(heur):
            out.append(heur[i])
    return out


# 토글 버튼 순서 — 데이터에 없어도 자리는 유지해 UI가 흔들리지 않게 한다
POS_ORDER = ["UTG", "MP", "CO", "BTN", "SB", "BB", "SB(BTN)"]
STACK_ORDER = ["pf", "short", "mid", "deep"]
STREET_ORDER = ["preflop", "flop", "turn", "river"]


def filter_options(db):
    """토글에 쓸 선택지. n은 그 선택지에 걸리는 스팟 수 (0이면 UI에서 흐리게).

    스트릿만 n이 None인데, 스팟에는 스트릿이 없고 핸드를 전부 파싱해야만 셀 수 있어서다
    (수만 핸드 × 매 토글 렌더 = 감당 안 됨). 세지 않고 흐리게 처리도 하지 않는다."""
    spots = leak_spots(db)
    pos_n, stack_n = {}, {}
    for s in spots:
        pos_n[_norm_pos(s.get("pos"))] = pos_n.get(_norm_pos(s.get("pos")), 0) + 1
        if s.get("stack"):
            stack_n[s["stack"]] = stack_n.get(s["stack"], 0) + 1
    return {
        "positions": [{"key": p, "label": p, "n": pos_n.get(p, 0)}
                      for p in POS_ORDER if p in pos_n or p != "SB(BTN)"],
        "stacks": [{"key": k, "label": STACK_LABEL[k], "n": stack_n.get(k, 0)}
                   for k in STACK_ORDER],
        "streets": [{"key": k, "label": STREET_KO[k], "n": None} for k in STREET_ORDER],
    }


def spots_view(db, positions=None, stacks=None, streets=None):
    """UI용 요약. 스팟 목록 자체는 UI에 노출하지 않으므로 개수만 보낸다
    (핸드 id 리스트는 수천 개라 절대 그대로 내보내면 안 된다)."""
    return {
        "matched": len(leak_spots(db, positions, stacks, streets)),
        "options": filter_options(db),
        "freq_available": freq_available(db),
        "total_hands": len(db.get("hands", {})),
        "scoreboard": scoreboard(db),
    }


# ---------------------------------------------------------------------------
# 출제
# ---------------------------------------------------------------------------

def _pick_decision(spot, decisions, streets=None):
    """스팟 성격에 맞는 결정 지점을 고른다. streets가 오면 그 스트릿 안에서만 고른다.

    스트릿 필터가 실제로 적용되는 지점이 여기다 — 스팟에는 스트릿이 없고, 핸드를
    파싱해야 비로소 '이 핸드에 리버 결정이 있나'를 알 수 있기 때문이다."""
    if streets:
        decisions = [d for d in decisions if d["street"] in streets]
    if not decisions:
        return None
    if spot["kind"] == "freq":
        return decisions[0]                       # 프리플랍 첫 결정
    if spot.get("reason") == "올인 패배":
        allins = [d for d in decisions if d["verb"] == "allin"]
        if allins:
            return allins[0]
    return decisions[-1]                          # 마지막(=가장 큰 커밋) 결정


def _choices(hand, dec):
    """그 시점에 실제로 가능한 액션들을 구체적인 금액과 함께 만든다."""
    def amt(x):
        return f"{convert.chips_str(x)} ({convert.bb(hand, x)})"

    tc, pot, stack = dec["to_call"], dec["pot_before"], dec["stack_before"]
    out = []
    if tc <= 0:
        out.append({"id": "check", "label": "체크", "amount": 0})
        for cid, frac, name in (("bet33", 1 / 3, "1/3 팟"), ("bet66", 2 / 3, "2/3 팟")):
            size = round(pot * frac)
            if 0 < size < stack:
                out.append({"id": cid, "label": f"{name} 벳 — {amt(size)}", "amount": size})
        if stack > 0:
            out.append({"id": "allin", "label": f"올인 — {amt(stack)}", "amount": stack})
    else:
        out.append({"id": "fold", "label": "폴드", "amount": 0})
        if tc >= stack:
            out.append({"id": "allin", "label": f"콜 (올인) — {amt(stack)}", "amount": stack})
        else:
            out.append({"id": "call", "label": f"콜 — {amt(tc)}", "amount": tc})
            target = round(pot + 2 * tc)          # 팟 사이즈 레이즈
            if target < stack:
                out.append({"id": "raise", "label": f"레이즈 to {amt(target)}", "amount": target})
            if stack > 0:
                out.append({"id": "allin", "label": f"올인 — {amt(stack)}", "amount": stack})
    return out


def _served_hands(db):
    return {a.get("hand_id") for a in _state(db)["attempts"] if a.get("hand_id")}


STREET_KO = {"preflop": "프리플랍", "flop": "플랍", "turn": "턴", "river": "리버"}


def next_question(db, spot_key=None, hero="Hero", positions=None, stacks=None,
                  streets=None):
    """다음 문제. 실제 핸드가 남아 있으면 그걸 쓰고, 소진됐으면 AI 생성을 요청한다.

    positions/stacks/streets는 히어로 포지션·시작 스택대·결정 스트릿 필터 (빈 값 = 전체).
    spot_key가 오면 그 스팟만 — 칩 클릭으로 특정 리크를 콕 집어 연습하는 경우다.

    반환: {"question": {...}} 또는 {"generate": {...}}  (후자는 gui가 AI로 만든다)
          거를 스팟이 없으면 {"error": ...}
    """
    spots = leak_spots(db, positions, stacks, streets)
    if not spots:
        if positions or stacks or streets:
            return {"error": "선택한 필터 조합에 해당하는 리크 스팟이 없습니다. "
                             "필터를 넓혀 보세요."}
        return {"error": "리크 스팟을 찾지 못했습니다. 핸드를 더 임포트하거나 분석해 보세요."}

    if spot_key:
        # 필터 밖의 스팟을 콕 집었을 수도 있으므로 전체에서 찾는다
        match = [s for s in leak_spots(db) if s["key"] == spot_key]
        order = [match[0]] if match else spots[:1]
    else:
        # 순위를 가중치로 뽑는다 — 큰 리크가 자주, 작은 리크도 가끔.
        # 두 축의 점수는 단위가 달라 직접 비교할 수 없으므로 leak_spots가 매겨준
        # 교차 순위를 쓴다 (점수를 그대로 쓰면 한쪽 축만 계속 뽑힌다).
        pool = spots[:12]
        order = []
        while pool and len(order) < MAX_SPOT_TRIES:
            i = random.choices(range(len(pool)),
                               weights=[len(pool) - k for k in range(len(pool))])[0]
            order.append(pool.pop(i))

    def gen_req(spot):
        """실제 핸드로 못 냈을 때 AI에게 넘길 요청 (스트릿 필터도 함께 전달)."""
        want = [STREET_KO[s] for s in STREET_ORDER if s in (streets or ())]
        return {"generate": {
            "key": spot["key"], "label": spot["label"], "detail": spot["detail"],
            "pos": spot.get("pos"), "stack": STACK_LABEL.get(spot.get("stack")),
            "reason": spot.get("reason"), "exhausted": len(spot["hands"]),
            "street": want[0] if len(want) == 1 else (", ".join(want) if want else None),
        }}

    served = _served_hands(db)
    # 스팟을 하나만 보고 포기하면 AI 생성으로 너무 쉽게 넘어간다. 스트릿 필터를 켜면
    # 구조적으로 그 스트릿에 도달할 수 없는 스팟(예: <15bb 올인 패배 + 리버)이 섞여 있어서,
    # 다음 순위 스팟들을 차례로 시도한 뒤에야 생성으로 폴백한다.
    for spot in order:
        fresh = [h for h in spot["hands"] if h not in served]
        # "실제 핸드에서 너무 많이 출제됐다" = 이 스팟의 미출제 핸드가 3개 미만
        if len(fresh) < 3:
            continue
        random.shuffle(fresh)
        for hid in fresh[:SCAN_PER_SPOT]:         # 파싱 실패/조건 불일치는 건너뛴다
            rec = db["hands"].get(hid)
            if not rec or not rec.get("raw"):
                continue
            try:
                hand = convert.parse_hand(rec["raw"])
            except Exception:
                continue
            decisions = convert.hero_decisions(hand, hero)
            dec = _pick_decision(spot, decisions, streets)
            if dec is None:
                continue                          # 이 핸드엔 해당 스트릿 결정이 없다
            choices = _choices(hand, dec)
            if len(choices) < 2:
                continue
            return {"question": {
                "source": "real",
                "hand_id": hid,
                "didx": dec["idx"],
                "spot": spot["key"],
                "spot_label": spot["label"],
                "street": STREET_KO.get(dec["street"], dec["street"]),
                "situation": convert.render_markdown(hand, hero, stop_at=dec["action"]),
                "choices": choices,
                "cached": cache_get(db, hid, dec["idx"], None) is not None,
            }}
    return gen_req(order[0])


def reveal(db, hand_id, didx, hero="Hero"):
    """채점이 끝난 뒤 공개할 '실제로는 이렇게 쳤고 결과는 이랬다'."""
    rec = db["hands"].get(hand_id)
    if not rec or not rec.get("raw"):
        return None
    try:
        hand = convert.parse_hand(rec["raw"])
    except Exception:
        return None
    decisions = convert.hero_decisions(hand, hero)
    if didx >= len(decisions):
        return None
    dec = decisions[didx]
    verb_ko = {"folds": "폴드", "checks": "체크", "calls": "콜", "bets": "벳",
               "raises": "레이즈", "allin": "올인"}
    size = dec["to_amount"] or dec["amount"]
    return {
        "actual": verb_ko.get(dec["verb"], dec["verb"]),
        "actual_amount": convert.chips_str(size) if size else "",
        "net_bb": rec.get("net_bb"),
        "full": convert.render_markdown(hand, hero),
        "analysis": rec.get("analysis"),
    }


# ---------------------------------------------------------------------------
# 채점 결과 캐시 · 응시 기록
# ---------------------------------------------------------------------------

def _cache_key(hand_id, didx, choice_id):
    return f"{hand_id}:{didx}:{choice_id}"


def cache_get(db, hand_id, didx, choice_id):
    """choice_id가 None이면 '이 결정 지점에 채점 이력이 있는지'만 확인."""
    cache = _state(db)["cache"]
    if choice_id is None:
        prefix = f"{hand_id}:{didx}:"
        return next((v for k, v in cache.items() if k.startswith(prefix)), None)
    return cache.get(_cache_key(hand_id, didx, choice_id))


def cache_put(db, hand_id, didx, choice_id, grade, text):
    cache = _state_mut(db)["cache"]
    cache[_cache_key(hand_id, didx, choice_id)] = {"grade": grade, "text": text}
    while len(cache) > MAX_CACHE:                 # 오래된 것부터 (dict는 삽입 순서 유지)
        cache.pop(next(iter(cache)))


def record_attempt(db, spot, hand_id, street, choice_id, grade, generated=False):
    q = _state_mut(db)
    q["attempts"].append({
        "ts": time.strftime("%Y-%m-%d %H:%M"),
        "spot": spot, "hand_id": hand_id, "street": street,
        "choice": choice_id, "grade": grade, "generated": generated,
    })
    if len(q["attempts"]) > MAX_ATTEMPTS:
        del q["attempts"][:len(q["attempts"]) - MAX_ATTEMPTS]


def scoreboard(db):
    attempts = _state(db)["attempts"]
    grades = {"좋음": 0, "무난": 0, "의문": 0, "실수": 0}
    for a in attempts:
        if a.get("grade") in grades:
            grades[a["grade"]] += 1
    graded = sum(grades.values())
    by_street = {}
    for a in attempts:
        st = a.get("street") or "?"
        s = by_street.setdefault(st, {"street": st, "n": 0, "ok": 0})
        s["n"] += 1
        if a.get("grade") in ("좋음", "무난"):
            s["ok"] += 1
    order = ["프리플랍", "플랍", "턴", "리버"]
    return {
        "total": len(attempts),
        "grades": grades,
        "ok_rate": round((grades["좋음"] + grades["무난"]) / graded * 100) if graded else None,
        "by_street": sorted(by_street.values(),
                            key=lambda s: order.index(s["street"]) if s["street"] in order else 9),
        "recent": attempts[-12:][::-1],
    }
