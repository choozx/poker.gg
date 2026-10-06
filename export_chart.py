#!/usr/bin/env python3
"""가져온 레인지 차트를 **파일 하나짜리 HTML**로 내보낸다 (폰에서 보기용).

차트 데이터를 전부 안에 박아 넣으므로 서버도 네트워크도 필요 없다 — 폰에 옮겨
브라우저로 열고 '홈 화면에 추가'하면 앱처럼 쓸 수 있고 오프라인에서도 동작한다.
(56장이 74KB뿐이라 APK로 감쌀 이유가 없다. 감싸더라도 이 파일을 띄우는 WebView다.)

사용법:
    python3 export_chart.py                      # ~/Desktop/range_charts.html
    python3 export_chart.py -o /path/out.html
    python3 export_chart.py --hero               # 내 실전 오픈율도 같이 넣기

**기본 출력 위치를 레포 밖(바탕화면)으로 둔 건 의도적이다** — 이 repo는 공개라,
GTO 툴에서 가져온 레인지가 들어간 파일을 커밋하면 그대로 인터넷에 공개된다.

표준 라이브러리만 쓴다 (프로젝트 규칙).
"""
import argparse
import json
import os

import cloud_sync
import ranges
import store

OUT_DEFAULT = "~/Desktop/range_charts.html"


def db_path(explicit=None):
    """gui.py와 같은 규칙 — 클라우드 모드면 로컬 캐시가 실체다."""
    if explicit:
        return explicit
    if cloud_sync.available():
        return os.path.expanduser("~/.cache/analyze_hand_history/hands_db.json")
    return "hands_db.json"


def collect(db, with_hero=False):
    """내보낼 데이터: {스팟: [{bb, label, source, cells}, …]} — 스택 오름차순.
    스팟은 오픈 차트면 포지션('UTG'), 방어 차트면 'BB vs BTN'이다 (버튼 하나씩)."""
    out = {}
    for slot in ranges.custom_slots(db):
        c = ranges.chart(slot["pos"], slot["stack"], db, slot["vs"])
        if not c:
            continue
        cells = {}
        for combo, w in c["weights"].items():
            w = float(w)
            if w <= 0.005:
                continue
            jam = min(float(c["jam"].get(combo, 0.0)), w)
            call = min(float(c["call"].get(combo, 0.0)), w)
            # [합계, 올인, 콜] 을 0~100 정수로 — 소수점은 폰 화면에서 의미가 없다
            cells[combo] = [round(w * 100), round(jam * 100), round(call * 100)]
        item = {"bb": slot["bb"], "label": slot["stack_label"],
                "vs": bool(slot["vs"]), "verb": c["verb"],
                "src": slot["source"] or "", "pct": slot["pct"],
                "jam": slot["jam_pct"], "call": round(c["call_pct"], 1),
                "cells": cells}
        if with_hero:
            # 오픈 차트면 [오픈, 기회], 방어 차트면 [방어(콜+3벳), 기회]
            rec = {k: [e[0], e[1]] for k, e in
                   ranges.hero_cells(db, slot["pos"], slot["bucket"], slot["vs"]).items() if e[1]}
            if rec:
                item["hero"] = rec
        out.setdefault(ranges.spot_name(slot["pos"], slot["vs"]), []).append(item)
    for v in out.values():
        v.sort(key=lambda x: (x["bb"] if x["bb"] is not None else -1))
    return out


PAGE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#14171c">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>레인지 차트</title>
<style>
  :root { --bg:#14171c; --panel:#1d2128; --border:#313845; --text:#d8dee8; --dim:#8a93a3;
          --accent:#ffa657; --raise:#dd4c45; --jam:#72271f; --call:#4f9a5c; --fold:#4d7bb3; }
  * { box-sizing:border-box; margin:0; padding:0; -webkit-tap-highlight-color:transparent; }
  body { background:var(--bg); color:var(--text); font:14px/1.5 -apple-system,
         "Apple SD Gothic Neo", "Noto Sans KR", sans-serif;
         padding:10px 10px calc(10px + env(safe-area-inset-bottom)); }
  h1 { font-size:15px; margin-bottom:8px; display:flex; align-items:baseline; gap:8px; }
  h1 b { color:var(--accent); font-size:17px; }
  h1 span { color:var(--dim); font-size:11px; font-weight:400; margin-left:auto; }
  .pos { display:flex; gap:5px; flex-wrap:wrap; margin-bottom:10px; }
  .pos button { flex:1 1 0; min-width:44px; padding:7px 2px; font-size:12.5px; font-weight:600;
                background:var(--panel); color:var(--dim); border:1px solid var(--border);
                border-radius:7px; }
  .pos button.on { border-color:var(--accent); color:var(--accent); background:rgba(255,166,87,.14); }
  .stack { margin-bottom:10px; }
  input[type=range] { -webkit-appearance:none; appearance:none; width:100%; height:4px;
                      border-radius:2px; background:var(--border); margin:10px 0 2px; }
  input[type=range]::-webkit-slider-thumb { -webkit-appearance:none; width:22px; height:22px;
                      border-radius:50%; background:var(--accent); }
  .ticks { position:relative; height:13px; margin:0 11px; }
  .ticks span { position:absolute; transform:translateX(-50%); font-size:9.5px; color:var(--dim); }
  .ticks span.on { color:var(--accent); font-weight:700; }
  .grid { display:grid; grid-template-columns:repeat(13,1fr); gap:1.5px; }
  .c { position:relative; aspect-ratio:1/1; border-radius:2px; overflow:hidden; background:var(--fold);
       display:flex; align-items:center; justify-content:center; font-size:2.4vw; font-weight:700;
       color:#fff; text-shadow:0 1px 2px rgba(0,0,0,.6); }
  .c i { position:absolute; top:0; bottom:0; font-style:normal; }
  .c .r { left:0; background:var(--raise); }
  .c .j { left:0; background:var(--jam); }
  .c .k { background:var(--call); }
  .c span { position:relative; }
  .c.pair { box-shadow:inset 0 0 0 1px rgba(255,255,255,.35); }
  .c.sel { box-shadow:inset 0 0 0 2px #fff; }
  .legend { display:flex; gap:10px; flex-wrap:wrap; margin-top:9px; color:var(--dim); font-size:11px; }
  .legend i { display:inline-block; width:9px; height:9px; border-radius:2px; margin-right:3px; }
  .detail { margin-top:8px; padding:8px 10px; background:var(--panel); border:1px solid var(--border);
            border-radius:8px; font-size:12.5px; min-height:36px; }
  .detail b { color:var(--accent); }
  .src { margin-top:7px; color:var(--dim); font-size:10.5px; }
  @media (min-width:560px) { .c { font-size:13px; } body { max-width:620px; margin:0 auto; } }
</style>
</head>
<body>
<h1>📊 <b id="title">—</b><span id="sum"></span></h1>
<div class="pos" id="pos"></div>
<div class="stack">
  <input type="range" id="sl" min="0" max="0" value="0" step="1">
  <div class="ticks" id="ticks"></div>
</div>
<div class="grid" id="grid"></div>
<div class="legend" id="legend"></div>
<div class="detail" id="detail">칸을 누르면 빈도가 나옵니다.</div>
<div class="src" id="src"></div>
<script>
const DATA = __DATA__;
const RANKS = 'AKQJT98765432'.split('');
const POS = Object.keys(DATA);
let pos = POS[0], si = 0, sel = null;

function combo(i, j) {
  const hi = RANKS[Math.min(i, j)], lo = RANKS[Math.max(i, j)];
  return i === j ? hi + lo : hi + lo + (i < j ? 's' : 'o');
}
// 스택을 바꿔도 같은 bb를 유지하려 애쓴다 — 포지션별로 같은 깊이를 비교하는 게 목적이다
function keepStack(prevBb) {
  const list = DATA[pos];
  if (prevBb == null) return 0;
  let best = 0;
  list.forEach((s, i) => {
    if (Math.abs((s.bb == null ? -1 : s.bb) - prevBb) <
        Math.abs((list[best].bb == null ? -1 : list[best].bb) - prevBb)) best = i;
  });
  return best;
}
function setPos(p) {
  const prev = (DATA[pos][si] || {}).bb;
  pos = p; si = keepStack(prev); sel = null; render();
}
function render() {
  const list = DATA[pos], cur = list[si];
  document.getElementById('pos').innerHTML = POS.map(p =>
    `<button class="${p === pos ? 'on' : ''}" onclick="setPos('${p}')">${p}</button>`).join('');
  document.getElementById('title').textContent = pos + ' · ' + cur.label;
  const mix = [cur.call ? '콜 ' + cur.call + '%' : '', cur.jam ? '올인 ' + cur.jam + '%' : '']
    .filter(Boolean).join(' · ');
  document.getElementById('sum').textContent = '액션 ' + cur.pct + '%' + (mix ? ' (' + mix + ')' : '');
  const sl = document.getElementById('sl');
  sl.max = list.length - 1; sl.value = si;
  document.getElementById('ticks').innerHTML = list.length < 2 ? '' : list.map((s, i) =>
    `<span style="left:${i / (list.length - 1) * 100}%" class="${i === si ? 'on' : ''}"
     >${s.bb == null ? s.label : s.bb}</span>`).join('');

  let g = '';
  for (let i = 0; i < 13; i++) for (let j = 0; j < 13; j++) {
    const c = combo(i, j), d = cur.cells[c] || [0, 0, 0];
    const w = d[0], jm = d[1], cl = d[2];
    g += `<div class="c${i === j ? ' pair' : ''}${sel === c ? ' sel' : ''}" onclick="tap('${c}')">` +
         (w - cl > 0 ? `<i class="r" style="width:${w - cl}%"></i>` : '') +
         (jm > 0 ? `<i class="j" style="width:${jm}%"></i>` : '') +
         (cl > 0 ? `<i class="k" style="left:${w - cl}%;width:${cl}%"></i>` : '') +
         `<span>${c}</span></div>`;
  }
  document.getElementById('grid').innerHTML = g;
  document.getElementById('legend').innerHTML =
    (cur.jam ? '<span><i style="background:var(--jam)"></i>올인</span>' : '') +
    `<span><i style="background:var(--raise)"></i>${cur.vs ? cur.verb : '레이즈'}</span>` +
    (cur.call ? '<span><i style="background:var(--call)"></i>콜</span>' : '') +
    '<span><i style="background:var(--fold)"></i>폴드</span>';
  document.getElementById('src').textContent = cur.src ? '출처: ' + cur.src : '';
  if (sel) tap(sel, true);
}
function tap(c, keep) {
  const cur = DATA[pos][si], d = cur.cells[c] || [0, 0, 0];
  sel = c;
  const parts = [];
  if (d[0] - d[1] - d[2] > 0) parts.push((cur.vs ? cur.verb : '레이즈') + ' ' + (d[0] - d[1] - d[2]) + '%');
  if (d[1] > 0) parts.push('올인 ' + d[1] + '%');
  if (d[2] > 0) parts.push('콜 ' + d[2] + '%');
  if (d[0] < 100) parts.push('폴드 ' + (100 - d[0]) + '%');
  let t = `<b>${c}</b> — ` + (parts.length ? parts.join(' · ') : '폴드 100%');
  const h = cur.hero && cur.hero[c];
  if (h) t += `<br><span style="color:var(--dim)">실전: ${h[1]}회 중 ${h[0]}회 ${cur.vs ? '방어' : '오픈'} ` +
              `(${Math.round(h[0] / h[1] * 100)}%)</span>`;
  document.getElementById('detail').innerHTML = t;
  if (!keep) render();
}
document.getElementById('sl').addEventListener('input', e => { si = +e.target.value; render(); });
render();
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description="레인지 차트를 폰에서 볼 HTML 한 장으로 내보낸다")
    ap.add_argument("-o", "--out", default=OUT_DEFAULT, help=f"출력 경로 (기본: {OUT_DEFAULT})")
    ap.add_argument("--db", help="핸드 DB 경로 (기본: 앱과 같은 위치)")
    ap.add_argument("--hero", action="store_true", help="내 실전 오픈 기록도 같이 넣기")
    a = ap.parse_args()

    path = db_path(a.db)
    if not os.path.exists(path):
        raise SystemExit(f"DB를 찾지 못했습니다: {path}")
    db = store.load_db(path)
    data = collect(db, with_hero=a.hero)
    if not data:
        raise SystemExit("내보낼 차트가 없습니다 — 먼저 GTO 차트를 가져오세요 (grab_chart.py).")

    out = os.path.expanduser(a.out)
    page = PAGE.replace("__DATA__", json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    n = sum(len(v) for v in data.values())
    print(f"✅ {out}  ({len(page.encode()) / 1024:.0f} KB)")
    print(f"   포지션 {len(data)}개 · 차트 {n}장" + (" · 내 실전 기록 포함" if a.hero else ""))
    print("   폰으로 옮겨 브라우저로 열고 '홈 화면에 추가'하면 앱처럼 쓸 수 있습니다 (오프라인 동작).")


if __name__ == "__main__":
    main()
