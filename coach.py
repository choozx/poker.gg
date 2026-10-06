#!/usr/bin/env python3
"""💬 AI 코치 — 내 플레이 기록을 근거로 AI와 대화한다.

다른 AI 기능(핸드 분석·종합 리포트·문제 채점)은 한 번 묻고 끝나지만, 여기는 대화가
이어진다. AI 호출 자체는 gui가 하고(백엔드 공용), 이 모듈은 세 가지만 맡는다:

  1. **내 플레이 요약** (`profile_text`) — 매 메시지에 붙는 컨텍스트. DB가 90MB라 핸드를
     통째로 넘길 수 없으므로 이미 얼려 둔 메타 필드만 모아 숫자로 요약한다 (AI 호출 없음).
     AI가 "내 BTN 오픈 넓어?"에 지어낸 숫자가 아니라 내 실제 숫자로 답하게 하는 부분.
  2. **핸드 참조** (`find_hand`) — 메시지에 `#번호`를 쓰면 그 핸드 원문 + 기존 분석을 붙인다.
     UI가 번호 끝 6자리만 보여주므로 끝자리 일치도 받는다 (유일할 때만).
  3. **대화 저장** — `db["coach"]["chats"]`, 최근 `MAX_CHATS`개 · 대화당 `MAX_MESSAGES`개.
     DB는 클라우드 동기화 대상이라 캡을 둔다 (quiz/ranges의 attempts 캡과 같은 이유).

백엔드(`claude -p` / API)는 둘 다 단발 호출이라, 대화는 최근 `HISTORY_TURNS`턴을 프롬프트에
다시 실어 이어 붙인다. 앞에서 짚은 핸드도 그 창 안에 있으면 다시 붙인다 — 그래야 "그 핸드에서
턴은?" 같은 후속 질문에 AI가 원문을 볼 수 있다.

의존 방향: convert ← store ← {quiz, ranges} ← coach ← gui
"""

import re
import time

import convert
import quiz
import ranges
import store

MAX_CHATS = 20          # DB에 남기는 대화 수 (오래 안 쓴 것부터 버린다)
MAX_MESSAGES = 60       # 대화 하나에 남기는 메시지 수
HISTORY_TURNS = 10      # 프롬프트에 다시 싣는 최근 턴 수 (1턴 = 질문 + 답)
MAX_REF_HANDS = 3       # 한 번에 붙이는 참조 핸드 수 (핸드 원문이 길다)
RECENT_HANDS = 2000     # '최근' 비교 구간 — 날짜 대신 핸드 수 (드문드문 쳐도 표본이 일정하게)
MIN_SPOT_N = 20         # 차트 비교 줄을 싣는 최소 기회 수 (그 아래는 노이즈)

HAND_REF_RE = re.compile(r"#(\d{6,})")


# ---------------------------------------------------------------------------
# 핸드 참조
# ---------------------------------------------------------------------------

def find_hand(db, ref):
    """'#번호'의 숫자 → hand_id (못 찾거나 끝자리가 여러 핸드에 걸리면 None)."""
    hands = db.get("hands", {})
    if ref in hands:
        return ref
    hits = [hid for hid in hands if hid.endswith(ref)]
    return hits[0] if len(hits) == 1 else None


def hand_refs(db, texts):
    """메시지들(최신 먼저)에서 짚은 핸드 → ([hand_id…], [못 찾은 번호…]). 최대 MAX_REF_HANDS개."""
    found, missing = [], []
    for t in texts:
        for ref in HAND_REF_RE.findall(t or ""):
            hid = find_hand(db, ref)
            if hid is None:
                if ref not in missing:
                    missing.append(ref)
            elif hid not in found:
                found.append(hid)
    return found[:MAX_REF_HANDS], missing


def _hand_block(db, hid, hero):
    rec = db["hands"][hid]
    md = convert.render_markdown(convert.parse_hand(rec["raw"]), hero=hero) if rec.get("raw") else ""
    out = [f"### 핸드 #{hid} ({rec.get('tournament_name') or ''})", md.strip()]
    if rec.get("analysis"):
        out += ["", "#### 이 핸드의 기존 AI 분석", rec["analysis"].strip()]
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 내 플레이 요약 — 매 메시지에 붙는 컨텍스트
# ---------------------------------------------------------------------------

def _pct(a, b):
    return f"{a / b * 100:.0f}%" if b else "—"


def _freqs(hands):
    """핸드 묶음 → VPIP / PFR / 오픈(RFI) 비율 문자열."""
    n = len(hands)
    vpip = sum(1 for h in hands if h.get("vpip"))
    pk = [h for h in hands if "pfr" in h]
    pfr = sum(1 for h in pk if h["pfr"])
    opp = [h for h in hands if h.get("pf_faced") == "none"]
    rfi = sum(1 for h in opp if h.get("rfi"))
    return (f"VPIP {_pct(vpip, n)} · PFR {_pct(pfr, len(pk))} · "
            f"오픈(RFI) {_pct(rfi, len(opp))} ({n:,}핸드)")


def _vs_chart_lines(db):
    """가져온/내장 오픈 차트 대비 내 실제 오픈율, 가져온 BB 방어 차트 대비 내 방어율.

    차트 기대치는 **내가 실제로 받은 조합**으로 가중한다 — 표본이 작으면 받은 패가 치우쳐
    있어서, 차트 전체 오픈%와 그냥 비교하면 차이가 패 운에서 나온다. 자리는 `_pos_8max`로
    8맥스 이름에 맞춘다 (가져온 차트가 8맥스라서)."""
    hands = db.get("hands", {})
    rfi, dev = {}, []
    for r in hands.values():
        if r.get("pf_faced") != "none":
            continue
        pos = ranges._pos_8max(r.get("hero_pos"), r.get("players"))
        sb = store._stack_bucket(r.get("stack_bb"))
        combo = store._combo(r.get("hero_cards") or [])
        if not pos or not sb or not combo:
            continue
        e = rfi.setdefault((pos, sb), {}).setdefault(combo, [0, 0])
        e[1] += 1
        if r.get("rfi"):
            e[0] += 1

    lines = []
    for (pos, sb), combos in sorted(rfi.items(), key=lambda kv: (
            ranges.POS_ORDER.index(kv[0][0]) if kv[0][0] in ranges.POS_ORDER else 99,
            ranges.STACK_ORDER.index(kv[0][1]))):
        c = ranges.chart(pos, sb, db)
        n = sum(e[1] for e in combos.values())
        if not c or n < MIN_SPOT_N:
            continue
        made = sum(e[0] for e in combos.values())
        exp = sum(e[1] * (c["weights"].get(k, 0.0) - c["call"].get(k, 0.0))
                  for k, e in combos.items())
        src = "가져온 차트" if c["source"] else "내장 근사"
        lines.append(f"- {pos} {ranges.STACK_LABEL[sb]}: 실제 {_pct(made, n)} / "
                     f"차트대로면 {_pct(exp, n)} (기회 {n}회, {src} {c['label']})")
        for k, (o, t) in combos.items():
            target = c["weights"].get(k, 0.0) - c["call"].get(k, 0.0)
            if t >= 3 and abs(o / t - target) >= 0.5:
                dev.append((abs(o / t - target) * min(1.0, t / 6), pos, sb, k, o, t, target))

    vs_lines = []
    if ranges.vs_personalized(db):
        agg = {}
        for (pos, sb, vs, combo), e in ranges._hero_vs(db).items():
            if pos == "BB":
                agg.setdefault((sb, vs), {})[combo] = e
        for (sb, vs), combos in sorted(agg.items(), key=lambda kv: (
                ranges.STACK_ORDER.index(kv[0][0]),
                ranges.POS_8MAX.index(kv[0][1]) if kv[0][1] in ranges.POS_8MAX else 99)):
            c = ranges.chart("BB", sb, db, vs)
            n = sum(e[1] for e in combos.values())
            if not c or n < MIN_SPOT_N:
                continue
            d = sum(e[0] for e in combos.values())
            r3 = sum(e[2] for e in combos.values())
            exp_d = sum(e[1] * c["weights"].get(k, 0.0) for k, e in combos.items())
            exp_r = sum(e[1] * (c["weights"].get(k, 0.0) - c["call"].get(k, 0.0))
                        for k, e in combos.items())
            vs_lines.append(f"- BB vs {vs} {ranges.STACK_LABEL[sb]}: 방어 {_pct(d, n)} "
                            f"(차트 {_pct(exp_d, n)}) · 3벳 {_pct(r3, n)} (차트 {_pct(exp_r, n)}) "
                            f"· 기회 {n}회")

    dev.sort(reverse=True)
    dev_lines = [f"- {pos} {ranges.STACK_LABEL[sb]} {k}: {t}회 중 {o}회 오픈 "
                 f"(차트 {target * 100:.0f}%)" for _, pos, sb, k, o, t, target in dev[:10]]
    return lines, vs_lines, dev_lines


def profile_text(db):
    """AI에게 넘길 '내 플레이 요약' 마크다운. 전부 로컬 집계 — AI 호출 없음.

    rebuild 전 DB면 `pf_faced`/`stack_bb`가 없어 차트 비교 줄이 비고, `pf_opener`가 없으면
    BB 방어 줄만 빈다 — 그때는 빈 이유를 적어 AI가 '데이터 없음'을 '문제 없음'으로 읽지 않게 한다."""
    hands = list(db.get("hands", {}).values())
    if not hands:
        return "(DB에 핸드가 없습니다)"
    st = store.stats(db)
    out = ["### 전체",
           f"- 핸드 {st['total']:,}개 · 토너 {st['tournaments']}개",
           f"- VPIP {st['vpip_pct']}% · PFR "
           f"{'—' if st['pfr_pct'] is None else str(st['pfr_pct']) + '%'} · "
           f"WTSD {st['wtsd_pct']}% · W$SD {st['wsd_pct']}%"]

    recent = sorted(hands, key=lambda h: h.get("datetime") or "")[-RECENT_HANDS:]
    if len(hands) > len(recent):
        out += ["", f"### 최근 {len(recent):,}핸드 vs 전체",
                f"- 최근: {_freqs(recent)}", f"- 전체: {_freqs(hands)}"]

    out += ["", "### 포지션별 (칩 EV는 플레이 품질 지표 — 상금 아님)"]
    for p in st["positions"]:
        out.append(f"- {p['pos']}: {p['hands']:,}핸드 · VPIP {_pct(p['vpip'], p['hands'])} · "
                   f"칩 {p['net_bb']:+.1f}bb ({p['net_bb'] / p['hands'] * 100:+.1f}bb/100)")

    personalized = ranges.personalized(db)
    if personalized:
        lines, vs_lines, dev_lines = _vs_chart_lines(db)
        out += ["", "### 오픈(폴드로 나에게 옴) — 실제 오픈율 vs 차트",
                *(lines or ["- (비교할 표본이 부족합니다)"])]
        if dev_lines:
            out += ["", "### 차트와 가장 어긋난 조합 (오픈)", *dev_lines]
        out += ["", "### BB 방어(오픈 한 번만 받음) — 실제 vs 가져온 차트"]
        if not ranges.vs_personalized(db):
            out.append("- (DB에 오프너 기록이 없음 — `--rebuild` 전이라 집계 불가)")
        else:
            out += vs_lines or ["- (가져온 BB 방어 차트가 없거나 표본이 부족합니다)"]
    else:
        out += ["", "### 차트 비교",
                "- (DB가 `--rebuild` 전이라 오픈/방어 기회를 판정할 수 없어 집계 불가)"]

    spots = quiz.leak_spots(db)[:8]
    if spots:
        out += ["", "### 앱이 찾은 약점 스팟 (빈도 이탈 기준선은 대략적인 MTT 참고값)"]
        out += [f"- {s['label']} — {s['detail']}" for s in spots]

    lr = store.leak_report(db)
    if lr["analyzed"]:
        ov = lr["overall"]
        out += ["", f"### 핸드별 AI 분석 결과 ({lr['analyzed']}핸드 분석됨)",
                "- 총평 등급: " + " · ".join(f"{g} {ov[g]}" for g in ("좋음", "무난", "의문", "실수"))]
        for x in lr["leak_hands"][:6]:
            out.append(f"- #{x['hand_id']} {x['hero_pos']} {''.join(x['hero_cards'])} "
                       f"{x['street']} [{x['grade']}] {x['snippet']}")

    rs, qs = ranges.scoreboard(db), quiz.scoreboard(db)
    if rs["total"] or qs["total"]:
        out += ["", "### 연습 성적"]
        if rs["total"]:
            out.append(f"- 레인지 드릴: {rs['total']}문제 · 정답률 {rs['ok_rate']}% "
                       f"(포지션별 " + ", ".join(f"{x['pos']} {_pct(x['ok'], x['n'])}"
                                              for x in rs["by_pos"]) + ")")
        if qs["total"]:
            out.append(f"- 핸드 리뷰 문제: {qs['total']}문제 · 정답률 {qs['ok_rate']}%")

    ts = store.tournament_list(db)["tournaments"][:5]
    out += ["", "### 최근 토너먼트"]
    out += [f"- {t['start'][:10]} {t['name']} (#{t['id']}, {t['hand_count']}핸드)" for t in ts]
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 프롬프트 조립
# ---------------------------------------------------------------------------

def build_prompt(db, chat_id, text, hero="Hero"):
    """(user 프롬프트, 참조한 hand_id 목록, 못 찾은 번호 목록).

    단발 백엔드로 대화를 이으려고 최근 HISTORY_TURNS턴을 그대로 다시 싣는다."""
    chat = get_chat(db, chat_id) or {"messages": []}
    history = chat["messages"][-HISTORY_TURNS * 2:]
    refs, missing = hand_refs(db, [text] + [m["text"] for m in reversed(history)
                                            if m["role"] == "user"])
    parts = ["## 내 플레이 요약 (앱이 내 DB에서 계산한 실제 숫자)", profile_text(db)]
    if refs:
        parts += ["", "## 참조 핸드 (사용자가 #번호로 짚은 핸드 원문)"]
        parts += [_hand_block(db, hid, hero) for hid in refs]
    if missing:
        parts += ["", f"(사용자가 짚었지만 DB에서 찾지 못한 번호: {', '.join('#' + m for m in missing)})"]
    if history:
        # 지난 답은 인용(>)으로 싣는다 — 답 안의 ## 제목이 이 프롬프트의 구역 제목과 섞이지 않게
        parts += ["", "## 지난 대화"]
        for m in history:
            quoted = "\n".join("> " + line for line in m["text"].splitlines())
            parts.append(f"**{'나' if m['role'] == 'user' else '코치'}:**\n{quoted}")
    parts += ["", "## 지금 질문", text]
    return "\n".join(parts), refs, missing


# ---------------------------------------------------------------------------
# 대화 저장
# ---------------------------------------------------------------------------

def _state(db):
    """읽기 전용 — **DB를 건드리지 않는다** (클라우드 푸시 중 최상위 키가 늘면 업로드가
    통째로 실패한다. quiz._state·ranges._state와 같은 이유)."""
    return (db.get("coach") or {}).get("chats") or []


def _state_mut(db):
    return db.setdefault("coach", {}).setdefault("chats", [])


def new_id():
    return str(int(time.time() * 1000))


def get_chat(db, chat_id):
    return next((c for c in _state(db) if c["id"] == chat_id), None) if chat_id else None


def chat_list(db):
    """대화 목록 (최근에 쓴 것 먼저). 본문은 빼고 제목·시각·메시지 수만."""
    out = [{"id": c["id"], "title": c["title"], "updated": c["updated"],
            "n": len(c["messages"])} for c in _state(db)]
    return sorted(out, key=lambda c: c["updated"], reverse=True)


def save_exchange(db, chat_id, text, reply, refs):
    """질문 + 답 한 쌍을 저장. 답이 끝까지 왔을 때만 부른다 (끊긴 답은 남기지 않는다).
    대화가 없으면 그 id로 새로 만든다 — 첫 질문이 실패해도 클라이언트는 같은 id로 다시 보낸다."""
    chats = _state_mut(db)
    now = time.strftime("%Y-%m-%d %H:%M:%S")      # 초까지 — 목록 정렬이 분 단위로 뭉치지 않게
    chat = get_chat(db, chat_id)
    if chat is None:
        title = re.sub(r"\s+", " ", HAND_REF_RE.sub(lambda m: "#" + m.group(1)[-6:], text)).strip()
        chat = {"id": chat_id, "title": title[:40] or "새 대화", "created": now,
                "updated": now, "messages": []}
        chats.append(chat)
    chat["messages"] += [{"role": "user", "text": text, "ts": now, "hands": refs},
                         {"role": "coach", "text": reply, "ts": now}]
    chat["updated"] = now
    del chat["messages"][:-MAX_MESSAGES]
    if len(chats) > MAX_CHATS:                       # 오래 안 쓴 대화부터 버린다
        chats.sort(key=lambda c: c["updated"])
        del chats[:len(chats) - MAX_CHATS]
    return chat


def delete_chat(db, chat_id):
    chats = _state_mut(db)
    n = len(chats)
    chats[:] = [c for c in chats if c["id"] != chat_id]
    return {"ok": len(chats) < n}


if __name__ == "__main__":                           # 요약 점검용: python3 coach.py [db경로]
    import sys
    print(profile_text(store.load_db(sys.argv[1] if len(sys.argv) > 1 else "hands_db.json")))
