"""핸드 DB (hands_db.json) 로드/저장/병합.

핸드 히스토리는 불변 데이터이므로 한 번 컨버팅한 결과를 JSON에 영구 저장한다.
- 키 = 핸드 번호 → 재임포트 시 중복 핸드는 스킵
- 원본 텍스트(raw)도 보관 → 컨버터 개선 시 --rebuild 로 전체 재변환 가능
- AI 분석 결과(analysis)도 핸드별로 저장
"""

import json
import os
import re
import threading
import time

from convert import hand_meta, parse_hand, split_hands

_SAVE_LOCK = threading.Lock()
HAND_ID_RE = re.compile(r"CoinPoker Hand #(\d+)")


def load_db(path):
    if not os.path.exists(path):
        return {"version": 1, "hands": {}}
    with open(path, encoding="utf-8") as f:
        db = json.load(f)
    db.setdefault("hands", {})
    return db


def save_db(path, db):
    """임시파일 작성 후 rename — 저장 중 중단되어도 기존 DB가 깨지지 않음."""
    with _SAVE_LOCK:
        db["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)


def build_record(raw, hero="Hero"):
    """원본 핸드 텍스트 → DB 레코드 (메타 + 마크다운 + 원본)."""
    h = parse_hand(raw)
    rec = hand_meta(h, hero)
    rec["tournament_id"] = h.tournament_id or "(cash)"
    rec["tournament_name"] = h.tournament or h.game
    rec["raw"] = raw.strip()
    return rec


def import_text(db, text, hero="Hero"):
    """텍스트의 핸드를 DB에 병합. 기존 핸드는 스킵. (added, skipped) 반환."""
    added = skipped = 0
    for raw in split_hands(text):
        m = HAND_ID_RE.match(raw.strip())
        if not m:
            continue
        hand_id = m.group(1)
        if hand_id in db["hands"]:
            skipped += 1
            continue
        db["hands"][hand_id] = build_record(raw, hero)
        added += 1
    return added, skipped


def rebuild(db, hero="Hero"):
    """저장된 원본(raw)으로 전체 재변환. AI 분석 결과는 유지."""
    for hand_id, rec in list(db["hands"].items()):
        new = build_record(rec["raw"], hero)
        if rec.get("analysis"):
            new["analysis"] = rec["analysis"]
        db["hands"][hand_id] = new


def tournament_list(db):
    """토너먼트 목록(핸드 본문 제외) — 최신 시작 시간 역순. 지연 로딩용."""
    groups = {}
    for rec in db["hands"].values():
        key = rec["tournament_id"]
        g = groups.setdefault(key, {
            "id": key, "name": rec["tournament_name"],
            "hand_count": 0, "analyzed": 0, "start": "", "end": "",
        })
        g["hand_count"] += 1
        if rec.get("analysis"):
            g["analyzed"] += 1
        dt = rec.get("datetime") or ""
        if dt:
            g["start"] = min(g["start"] or dt, dt)
            g["end"] = max(g["end"], dt)
    tournaments = sorted(groups.values(), key=lambda t: t["start"], reverse=True)
    return {"tournaments": tournaments}


def review_hands(db):
    """복기 추천 핸드 전체 (raw 제외, 최신순)."""
    hands = [
        {k: v for k, v in rec.items() if k != "raw"}
        for rec in db["hands"].values()
        if rec.get("review")
    ]
    hands.sort(key=lambda h: h.get("datetime") or "", reverse=True)
    return {"hands": hands}


def tournament_hands(db, tournament_id):
    """특정 토너먼트의 핸드 목록 (raw 제외, 시간순)."""
    hands = [
        {k: v for k, v in rec.items() if k != "raw"}
        for rec in db["hands"].values()
        if rec["tournament_id"] == tournament_id
    ]
    hands.sort(key=lambda h: h.get("datetime") or "")
    return {"id": tournament_id, "hands": hands}


# 포지션 정렬 순서 (얼리 → 레이트 → 블라인드)
_POS_ORDER = ["UTG", "UTG+1", "MP1", "MP2", "MP3", "MP", "HJ", "CO",
              "BTN", "SB(BTN)", "SB", "BB"]


def _pos_key(pos):
    base = (pos or "?").split("(")[0]
    for cand in (pos, base):
        if cand in _POS_ORDER:
            return _POS_ORDER.index(cand)
    return len(_POS_ORDER) + 1


def stats(db):
    """전체 핸드 집계 통계 (통계 대시보드용). raw 파싱 없이 메타 필드만 사용."""
    hands = list(db["hands"].values())
    total = len(hands)
    by_pos, tids = {}, set()
    vpip = pfr = pfr_known = showdown = showdown_won = 0

    for h in hands:
        net = h.get("net") or 0
        nb = h.get("net_bb")
        tids.add(h.get("tournament_id"))
        if h.get("vpip"):
            vpip += 1
        if "pfr" in h:                       # 구 DB(rebuild 전)는 pfr 키 없음 → 집계 제외
            pfr_known += 1
            if h["pfr"]:
                pfr += 1
        if h.get("showdown"):
            showdown += 1
            if net > 0:
                showdown_won += 1

        # 포지션별 칩 EV(bb) — 플레이 품질 지표 (상금 아님)
        pos = h.get("hero_pos") or "?"
        p = by_pos.setdefault(pos, {"pos": pos, "hands": 0, "vpip": 0, "net_bb": 0.0})
        p["hands"] += 1
        if h.get("vpip"):
            p["vpip"] += 1
        if nb is not None:
            p["net_bb"] += nb

    positions = sorted(by_pos.values(), key=lambda p: _pos_key(p["pos"]))
    for p in positions:
        p["net_bb"] = round(p["net_bb"], 1)

    return {
        "total": total,
        "tournaments": len(tids),
        "vpip_pct": round(100 * vpip / total) if total else 0,
        "pfr_pct": round(100 * pfr / pfr_known) if pfr_known else None,
        "pfr_known": pfr_known,
        "wtsd_pct": round(100 * showdown / vpip) if vpip else 0,
        "wsd_pct": round(100 * showdown_won / showdown) if showdown else 0,
        "showdown": showdown,
        "positions": positions,
    }
