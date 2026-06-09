"""뱅크롤(실제 돈) 관리 — 핸드 데이터(칩 EV)와 별개 도메인.

- 토너 결과(바이인/상금/손익)를 앱이 직접 보관: db["bankroll"]["entries"].
- 기존 구글시트('토너먼트 기록' 탭)를 1회 이주(migrate)로 seed.
- 각 엔트리는 핸드 DB 토너(tournament_id)에 매칭 → 돈 결과 ↔ 플레이 품질 조인.
- 매칭은 *링크*일 뿐 *필터*가 아님 — 돈 합계는 매칭과 무관하게 전 엔트리를 집계.

이주 이후엔 앱에서 직접 추가/수정(add_entry/update_entry)하며, 시트는 더 안 봐도 됨.
"""

import datetime
import io
import re
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict

# 구글시트 (링크 공유) — 워크북 전체를 xlsx로 받아 '토너먼트 기록' 탭만 파싱
SHEET_ID = "1XbQ9qwYL0PYFZR8BFB-8a-aP-nmsoqltJPhRGBH4mKc"
RECORDS_TAB = "토너먼트 기록"

_BUYIN_RE = re.compile(r"₮\s*([0-9]+(?:\.[0-9]+)?)")
_M = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


# ---------------------------------------------------------------------------
# 이름/숫자 정규화 & 매칭
# ---------------------------------------------------------------------------

_SAT_RE = re.compile(r"seats?\s+to|sat\s+to|\bstep\b|ticket|freeroll|\btmt\b", re.I)


def is_satellite(name):
    """새틀라이트/티켓/스텝성 토너 — 이름의 ₮는 *목적지* 값이라 바이인이 아님."""
    return bool(_SAT_RE.search(name or ""))


def parse_buyin(name):
    """토너 이름의 ₮ 바이인 추출. 새틀라이트는 이름 ₮가 목적지값이라 신뢰 불가 → 0."""
    if is_satellite(name):
        return 0.0
    m = _BUYIN_RE.search(name or "")
    return float(m.group(1)) if m else 0.0


_TICKET_STOP = {"the", "to", "seats", "seat", "step", "sat", "x", "tmt", "tickets", "added", "via"}
_SAT_TGT_RE = re.compile(r"to\s+₮?([\d.]+)\s+(.+)$", re.I)


def _tokens(name):
    s = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    return {w for w in s.split()
            if w and w not in _TICKET_STOP and not w.replace(".", "").isdigit()}


def _satellite_targets(entries):
    """세틀 엔트리들에서 (목적지 바이인값, 목적지 토큰셋, 날짜) 추출."""
    out = []
    for e in entries:
        if not is_satellite(e.get("name")):
            continue
        m = _SAT_TGT_RE.search(e["name"])
        if m:
            out.append((float(m.group(1)), _tokens(m.group(2)), e.get("date", "")))
    return out


def is_ticket_entry(name, buyin, date, sat_targets):
    """이 토너가 세틀에서 딴 티켓으로 올라간 본토너인지 — 목적지 토큰 포함 + 바이인값 일치 + 날짜 인접."""
    ut = _tokens(name)
    return any(tt and tt <= ut and abs(val - buyin) < 0.5 and _days_apart(d, date) <= 5
               for val, tt, d in sat_targets)


def _norm(s):
    """매칭용 이름 정규화 — LUS:/₮/$ 제거, 5.50→5.5, 영숫자만."""
    s = re.sub(r"^(LUS:?\s*)", "", s or "").replace("₮", "").replace("$", "")
    s = re.sub(r"\d+\.\d+", lambda m: str(float(m.group())).rstrip("0").rstrip("."), s)
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _date_only(s):
    """'2026/06/04 22:44 KST' 또는 ISO → 'YYYY-MM-DD'."""
    return (s or "")[:10].replace("/", "-")


def _session_date(dt_str, cutoff=5):
    """첫 핸드 시각 → 세션(토너 시작) 날짜. 새벽 cutoff시 이전이면 전날로 — 심야 토너는
    자정을 넘겨 첫 핸드가 00:0x에 찍혀도 사용자는 시작일(전날)로 기록하므로 매칭에 사용."""
    try:
        d = datetime.datetime.strptime((dt_str or "")[:19], "%Y/%m/%d %H:%M:%S")
        if d.hour < cutoff:
            d -= datetime.timedelta(days=1)
        return d.date().isoformat()
    except ValueError:
        return _date_only(dt_str)


def _days_apart(a, b):
    try:
        return abs((datetime.date.fromisoformat(a) - datetime.date.fromisoformat(b)).days)
    except ValueError:
        return 999


def _match_key(name):
    """매칭 그룹 키. 프리롤은 이름이 제각각('freeroll'/'Level Up Freeroll'/'Road to Triton…')이라
    하나의 'freeroll' 키로 묶어 날짜/크기로 정렬되게 함."""
    if re.search(r"freeroll", name or "", re.I):
        return "freeroll"
    return _norm(name)


def _ranks(vals):
    """값들을 오름차순 0~1 정규화 순위로. (같은 날 멀티파이어를 크기로 정렬하는 타이브레이크용)"""
    n = len(vals)
    order = sorted(range(n), key=lambda i: vals[i])
    rank = [0.0] * n
    for pos, i in enumerate(order):
        rank[i] = pos / (n - 1) if n > 1 else 0.0
    return rank


def hand_tournaments(db):
    """핸드 DB를 토너 단위로 집계: {tid: {id,name,start,hands,net_bb}}.

    net_bb = 그 토너 전체 칩 EV 합(플레이 품질). 돈과 별개."""
    agg = {}
    for r in db["hands"].values():
        tid = r.get("tournament_id")
        a = agg.setdefault(tid, {"id": tid, "name": r.get("tournament_name"),
                                 "start": "", "hands": 0, "net_bb": 0.0})
        a["hands"] += 1
        nb = r.get("net_bb")
        if nb is not None:
            a["net_bb"] += nb
        dt = r.get("datetime") or ""
        if dt:
            a["start"] = min(a["start"] or dt, dt)
    for a in agg.values():
        a["net_bb"] = round(a["net_bb"], 1)
    return agg


def _build_index(ht):
    """match_key -> [(date, tid)] (매칭용 역색인)."""
    idx = {}
    for tid, a in ht.items():
        idx.setdefault(_match_key(a["name"]), []).append((_session_date(a["start"]), tid))
    return idx


def match_tid(idx, name, date):
    """이름+날짜로 tid 찾기 (수동 단건 입력용). 같은 날 우선, 없으면 ±1일 단일 후보."""
    cands = idx.get(_match_key(name), [])
    if not cands:
        return None
    same = [tid for d, tid in cands if d == date]
    if same:
        return same[0]
    near = [tid for d, tid in cands if d and date and _days_apart(d, date) <= 1]
    return near[0] if len(near) == 1 else None


def _align(sheet_dates, sheet_cash, hand_dates, hand_hands, gap=6, max_match=12):
    """순서보존 정렬 (Needleman-Wunsch). 둘 다 시간순일 때 i↔j를 날짜 근접+순서 유지로 짝지음.

    시트는 사용자가 시간순 정리(같은날은 위가 먼저), 핸드는 실제 딜링 시각순.
    - 매칭 비용 = 날짜차 + 0.4·(1−핸드수순위): 같은 날 후보 중 핸드 많은 인스턴스 선호
      (프리롤 $2 입상 → 1핸드 버스트 말고 123핸드 딥런에 매칭).
    - 입상(cash>0) 시트행은 갭 비용을 키워 갭 회피: 딥런은 핸드가 있으니 매칭을 강제
      → 같은 날 [입상행 + 버스트행] vs [핸드 1판]이면 입상행이 핸드를 가져감.
    타이브레이크가 1 미만이라 날짜가 항상 우선. {시트인덱스: 핸드인덱스} 반환."""
    n, m = len(sheet_dates), len(hand_dates)
    hr = _ranks(hand_hands)
    INF = float("inf")

    def mcost(i, j):
        d = _days_apart(sheet_dates[i], hand_dates[j])
        return INF if d > max_match else d + 0.4 * (1 - hr[j])

    def sgap(i):                          # 시트행 i를 갭(미매칭)으로 둘 때 비용
        return gap * (2.5 if sheet_cash[i] > 0 else 1.0)

    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0] = dp[i - 1][0] + sgap(i - 1)
    for j in range(1, m + 1):
        dp[0][j] = j * gap
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            dp[i][j] = min(dp[i - 1][j - 1] + mcost(i - 1, j - 1),
                           dp[i - 1][j] + sgap(i - 1), dp[i][j - 1] + gap)
    res = {}
    i, j = n, m
    while i > 0 and j > 0:
        if dp[i][j] == dp[i - 1][j - 1] + mcost(i - 1, j - 1):
            res[i - 1] = j - 1
            i -= 1
            j -= 1
        elif dp[i][j] == dp[i - 1][j] + sgap(i - 1):
            i -= 1
        else:
            j -= 1
    return res


# ---------------------------------------------------------------------------
# 구글시트(xlsx) 읽기
# ---------------------------------------------------------------------------

def fetch_workbook():
    """링크 공유된 시트를 xlsx 바이트로 받음 (stdlib urllib, 리다이렉트 자동 추적)."""
    url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=xlsx"
    with urllib.request.urlopen(url, timeout=60) as resp:
        return resp.read()


def _read_sheet(xlsx_bytes, tab_name):
    """xlsx 바이트에서 지정 탭의 행들을 [[셀,...], ...]로. (zipfile+xml, 무의존성)"""
    z = zipfile.ZipFile(io.BytesIO(xlsx_bytes))
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        sst = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in sst.findall(f"{{{_M}}}si"):
            shared.append("".join(t.text or "" for t in si.iter(f"{{{_M}}}t")))
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    relmap = {r.get("Id"): r.get("Target") for r in rels}
    target = None
    for s in wb.iter(f"{{{_M}}}sheet"):
        if s.get("name") == tab_name:
            target = relmap.get(s.get(f"{{{_R}}}id"))
            break
    if not target:
        raise ValueError(f"탭 '{tab_name}'을 찾을 수 없습니다.")

    def colnum(ref):
        c = "".join(ch for ch in ref if ch.isalpha())
        n = 0
        for ch in c:
            n = n * 26 + (ord(ch) - 64)
        return n - 1

    sh = ET.fromstring(z.read("xl/" + target))
    rows = []
    for row in sh.iter(f"{{{_M}}}row"):
        cells = {}
        for c in row.findall(f"{{{_M}}}c"):
            t, v = c.get("t"), c.find(f"{{{_M}}}v")
            val = shared[int(v.text)] if (t == "s" and v is not None) else (v.text if v is not None else "")
            cells[colnum(c.get("r"))] = val
        rows.append([cells.get(i, "") for i in range(max(cells) + 1)] if cells else [])
    return rows


def _fnum(x):
    try:
        return float(str(x).replace(",", ""))
    except (ValueError, AttributeError):
        return 0.0


def _serial_to_iso(s):
    try:
        return (datetime.date(1899, 12, 30) + datetime.timedelta(days=int(float(s)))).isoformat()
    except (ValueError, TypeError):
        return ""


def parse_records(xlsx_bytes):
    """'토너먼트 기록' 탭 → 엔트리 dict 리스트 (헤더로 컬럼 매핑, 빈 행 제외)."""
    rows = _read_sheet(xlsx_bytes, RECORDS_TAB)
    # 헤더 행 찾기 ('토너먼트명' 포함)
    hi = next((i for i, r in enumerate(rows) if any("토너먼트명" in str(c) for c in r)), None)
    if hi is None:
        raise ValueError("'토너먼트명' 헤더를 찾지 못했습니다.")
    header = [str(c).strip() for c in rows[hi]]

    def col(key):
        return next((i for i, h in enumerate(header) if h.startswith(key)), None)

    ci = {k: col(k) for k in ["날짜", "토너먼트명", "바이인", "바이인/리바이", "총 비용",
                              "순위", "상금", "손익", "메모"]}

    def cell(r, key):
        i = ci[key]
        return r[i] if (i is not None and i < len(r)) else ""

    out = []
    for r in rows[hi + 1:]:
        name = str(cell(r, "토너먼트명")).strip()
        if not name:
            continue
        cost = _fnum(cell(r, "총 비용"))
        cash = _fnum(cell(r, "상금"))
        out.append({
            "date": _serial_to_iso(cell(r, "날짜")),
            "name": name,
            "buyin": _fnum(cell(r, "바이인")),
            "entries": int(_fnum(cell(r, "바이인/리바이")) or 1),
            "cost": cost,
            "rank": str(cell(r, "순위")).strip(),
            "cash": cash,
            "pnl": round(cash - cost, 2),
            "memo": str(cell(r, "메모")).strip(),
        })
    return out


# ---------------------------------------------------------------------------
# 엔트리 저장/이주
# ---------------------------------------------------------------------------

def _bank(db):
    return db.setdefault("bankroll", {"currency": "USD", "next_id": 1, "entries": []})


def _new_id(b):
    i = b.get("next_id", 1)
    b["next_id"] = i + 1
    return f"b{i}"


def migrate_from_sheet(db, xlsx_bytes=None):
    """시트 '토너먼트 기록'을 db["bankroll"]로 이주(전체 교체). 핸드 토너에 자동 매칭.

    (added, matched, unmatched) 반환. 기존 엔트리는 덮어씀(seed 성격)."""
    if xlsx_bytes is None:
        xlsx_bytes = fetch_workbook()
    records = parse_records(xlsx_bytes)            # 시간순 (시트 행 순서)
    ht = hand_tournaments(db)

    # 이름별 핸드 토너 (시작시각순) / 시트 레코드(이미 시간순) 그룹핑.
    # match_key로 묶음(프리롤은 하나의 'freeroll' 그룹). 핸드는 (날짜, 핸드수), 시트는 상금을
    # 정렬 타이브레이크로 사용 → 같은 날 딥런↔딥런 매칭.
    hand_groups = defaultdict(list)
    for tid, a in ht.items():
        hand_groups[_match_key(a["name"])].append((_session_date(a["start"]), tid, a["hands"]))
    for g in hand_groups.values():
        g.sort()
    sheet_groups = defaultdict(list)
    for i, rec in enumerate(records):
        sheet_groups[_match_key(rec["name"])].append(i)

    # 이름 그룹마다 순서보존 정렬로 시트행 → tid 배정
    tid_for = {}
    for key, sidxs in sheet_groups.items():
        hands = hand_groups.get(key)
        if not hands:
            continue
        amap = _align([records[i]["date"] for i in sidxs], [records[i]["cash"] for i in sidxs],
                      [d for d, _, _ in hands], [hn for _, _, hn in hands])
        for k, hk in amap.items():
            tid_for[sidxs[k]] = hands[hk][1]

    # 수동 강제 링크는 재이주에도 유지 (key = "date|name")
    overrides = (db.get("bankroll") or {}).get("overrides", {})
    b = {"currency": "USD", "next_id": 1, "entries": [], "overrides": overrides}
    for i, rec in enumerate(records):
        tid = overrides.get(f"{rec['date']}|{rec['name']}", tid_for.get(i))
        b["entries"].append(dict(rec, id=_new_id(b), tournament_id=tid, source="sheet"))
    matched = sum(1 for e in b["entries"] if e.get("tournament_id"))
    db["bankroll"] = b
    return len(records), matched, len(records) - matched


def set_override(db, date, name, tid):
    """빈 핸드(미매칭) 엔트리를 특정 토너에 강제 링크. 재이주에도 유지됨(앞으로 시트 연동 안 함).
    tid=None 으로 호출하면 강제 링크 해제."""
    b = _bank(db)
    ov = b.setdefault("overrides", {})
    k = f"{date}|{name}"
    if tid is None:
        ov.pop(k, None)
    else:
        ov[k] = tid
    n = 0
    for e in b["entries"]:
        if e.get("date") == date and e.get("name") == name:
            e["tournament_id"] = tid
            n += 1
    return n


def add_entry(db, fields):
    """수동 엔트리 추가 (토너 종료 후 입력). cost/pnl/tid 자동 보정 후 저장."""
    b = _bank(db)
    e = _normalize_entry(db, dict(fields))
    e["id"] = _new_id(b)
    e["source"] = e.get("source") or "manual"
    b["entries"].append(e)
    return e


def update_entry(db, entry_id, fields):
    b = _bank(db)
    for i, e in enumerate(b["entries"]):
        if e["id"] == entry_id:
            merged = _normalize_entry(db, {**e, **fields, "id": entry_id})
            b["entries"][i] = merged
            return merged
    return None


def delete_entry(db, entry_id):
    b = _bank(db)
    n = len(b["entries"])
    b["entries"] = [e for e in b["entries"] if e["id"] != entry_id]
    return len(b["entries"]) < n


def _normalize_entry(db, e):
    """입력 보정: buyin 자동(이름), cost=buyin*entries(미지정 시), pnl=cash-cost,
    tournament_id 미지정 시 자동 매칭 시도."""
    e["name"] = (e.get("name") or "").strip()
    e["date"] = _date_only(e.get("date") or "")
    e["entries"] = int(e.get("entries") or 1)
    e["buyin"] = float(e.get("buyin") or parse_buyin(e["name"]))
    e["cost"] = float(e["cost"]) if e.get("cost") not in (None, "") else round(e["buyin"] * e["entries"], 2)
    e["cash"] = float(e.get("cash") or 0)
    e["pnl"] = round(e["cash"] - e["cost"], 2)
    e["rank"] = str(e.get("rank") or "").strip()
    e["memo"] = str(e.get("memo") or "").strip()
    if not e.get("tournament_id"):
        idx = _build_index(hand_tournaments(db))
        e["tournament_id"] = match_tid(idx, e["name"], e["date"])
    return e


# ---------------------------------------------------------------------------
# 집계 (뱅크롤 대시보드용)
# ---------------------------------------------------------------------------

def summary(db):
    """뱅크롤 요약 + 엔트리(핸드 조인) + 미매칭/역방향 패널 데이터."""
    b = db.get("bankroll") or {"currency": "USD", "entries": []}
    ht = hand_tournaments(db)
    entries = sorted(b.get("entries", []), key=lambda e: (e.get("date") or "", e.get("id")))

    total_cost = sum(e["cost"] for e in entries)
    total_cash = sum(e["cash"] for e in entries)
    paid = [e for e in entries if e["cost"] > 0]              # 프리롤 제외 (ITM% 분모)
    itm = [e for e in paid if e["cash"] > 0]

    rows, cum = [], 0.0
    for e in entries:
        cum = round(cum + e["pnl"], 2)
        t = ht.get(e.get("tournament_id"))
        rows.append({**e, "cum_pnl": cum,
                     "hands": t["hands"] if t else 0,
                     "net_bb": t["net_bb"] if t else None})   # 그 토너 칩 EV

    matched_tids = {e.get("tournament_id") for e in entries if e.get("tournament_id")}
    # 역방향: 핸드는 있는데 엔트리 없는 토너. 세틀 티켓으로 올라간 본토너(버스트→기록불필요)는
    # ticket=True로 구분 — 돈 누락이 아니라 정상.
    sat_targets = _satellite_targets(entries)
    unlogged = []
    for tid, t in ht.items():
        if tid in matched_tids or not tid:
            continue
        bi = parse_buyin(t["name"])
        d = _date_only(t["start"])
        unlogged.append({"id": t["id"], "name": t["name"], "start": d,
                         "hands": t["hands"], "net_bb": t["net_bb"], "buyin": bi,
                         "ticket": is_ticket_entry(t["name"], bi, d, sat_targets)})
    unlogged.sort(key=lambda x: x["start"], reverse=True)

    return {
        "currency": b.get("currency", "USD"),
        "profit": round(total_cash - total_cost, 2),
        "total_cost": round(total_cost, 2),
        "total_cash": round(total_cash, 2),
        "roi": round(100 * (total_cash - total_cost) / total_cost, 1) if total_cost else 0,
        "itm_pct": round(100 * len(itm) / len(paid)) if paid else 0,
        "n": len(entries),
        "n_paid": len(paid),
        "avg_buyin": round(total_cost / sum(e["entries"] for e in paid), 2) if paid else 0,
        "biggest_cash": max((e["cash"] for e in entries), default=0),
        "unmatched": [r for r in rows if not r.get("tournament_id")],
        "unlogged": unlogged,
        "entries": rows,
    }


# ---------------------------------------------------------------------------
# CLI: 1회 이주
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import store

    ap = argparse.ArgumentParser(description="뱅크롤 시트 이주")
    ap.add_argument("--db", default="hands_db.json")
    ap.add_argument("--dry", action="store_true", help="저장 없이 통계만")
    ap.add_argument("--xlsx", help="로컬 xlsx 경로 (없으면 시트 다운로드)")
    args = ap.parse_args()

    db = store.load_db(args.db)
    data = open(args.xlsx, "rb").read() if args.xlsx else fetch_workbook()
    added, matched, unmatched = migrate_from_sheet(db, data)
    s = summary(db)
    print(f"이주: {added}엔트리 · 매칭 {matched} · 미매칭 {unmatched}")
    print(f"순손익 ${s['profit']} · ROI {s['roi']}% · ITM {s['itm_pct']}% · "
          f"비용 ${s['total_cost']} · 상금 ${s['total_cash']}")
    print(f"미매칭 {len(s['unmatched'])}건 · 역방향(시트누락) {len(s['unlogged'])}건")
    if not args.dry:
        store.save_db(args.db, db)
        print(f"저장 완료 → {args.db}")
