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


def tournaments_response(db):
    """DB → 웹앱 응답 구조. 토너먼트는 최신 시작 시간 역순 정렬."""
    groups = {}
    for rec in db["hands"].values():
        key = rec["tournament_id"]
        g = groups.setdefault(key, {
            "id": key, "name": rec["tournament_name"], "hands": [],
        })
        g["hands"].append({k: v for k, v in rec.items() if k != "raw"})
    tournaments = []
    for g in groups.values():
        g["hands"].sort(key=lambda h: h.get("datetime") or "")
        times = [h["datetime"] for h in g["hands"] if h.get("datetime")]
        g["start"] = times[0] if times else ""
        g["end"] = times[-1] if times else ""
        g["hand_count"] = len(g["hands"])
        tournaments.append(g)
    tournaments.sort(key=lambda t: t["start"], reverse=True)
    return {"tournaments": tournaments}
