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
    """내보낼 데이터: {"자리|상대": [{bb, label, src, pct, jam, call, cells, hero?}, …]} — 스택 오름차순.

    키는 앱의 (pos, vs)와 같다 — 오픈 차트는 'UTG|', 방어 차트는 'BB|BTN', 'BB|BTN-allin',
    'BB|SB-limp'. 페이지가 앱과 같은 액션 카드 바로 (자리, 상대)를 골라 찾는다."""
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
                "src": slot["source"] or "", "pct": slot["pct"],
                "jam": slot["jam_pct"], "call": round(c["call_pct"], 1),
                "cells": cells}
        if with_hero:
            # 오픈 차트면 [오픈, 기회], 방어 차트면 [방어, 기회] (올인을 받으면 콜, 림프를 받으면 레이즈)
            rec = {k: [e[0], e[1]] for k, e in
                   ranges.hero_cells(db, slot["pos"], slot["vs"], slot=slot["bb"]).items()
                   if e[1]} if slot["bb"] is not None else {}
            if rec:
                item["hero"] = rec
        out.setdefault(f"{slot['pos']}|{slot['vs'] or ''}", []).append(item)
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
  :root { --bg:#14171c; --panel:#1d2128; --panel2:#262b34; --border:#313845; --text:#d8dee8; --dim:#8a93a3;
          --accent:#ffa657; --raise:#dd4c45; --jam:#72271f; --call:#4f9a5c; --fold:#4d7bb3; }
  * { box-sizing:border-box; margin:0; padding:0; -webkit-tap-highlight-color:transparent; }
  body { background:var(--bg); color:var(--text); font:14px/1.5 -apple-system,
         "Apple SD Gothic Neo", "Noto Sans KR", sans-serif;
         padding:10px 10px calc(10px + env(safe-area-inset-bottom)); }
  h1 { font-size:15px; margin:8px 0; display:flex; align-items:baseline; gap:8px; flex-wrap:wrap; }
  h1 b { color:var(--accent); font-size:17px; }
  h1 span { color:var(--dim); font-size:11px; font-weight:400; }
  .max { display:flex; gap:4px; margin-bottom:8px; }
  .max button { flex:1 1 0; padding:6px 0; font-size:12px; font-weight:600; background:var(--panel);
                color:var(--dim); border:1px solid var(--border); border-radius:7px; }
  .max button.on { border-color:var(--accent); color:var(--accent); background:rgba(255,166,87,.14); }
  .bar { display:flex; gap:5px; overflow-x:auto; padding-bottom:6px; margin-bottom:4px; position:relative; }
  .card { flex:0 0 auto; min-width:68px; background:var(--panel); border:1px solid var(--border);
          border-radius:8px; padding:5px 6px; }
  .card.cur { border-color:var(--accent); box-shadow:0 0 0 1px var(--accent) inset; }
  .card.later { opacity:.5; }
  .card.none .pn { color:var(--dim); }
  .pn { font-weight:700; font-size:12px; margin-bottom:3px; display:flex; gap:4px; align-items:baseline; }
  .pn i { font-style:normal; font-size:9px; font-weight:600; color:var(--raise); }
  .pn u { text-decoration:none; font-size:9px; font-weight:500; color:var(--dim); }
  .chip { display:flex; justify-content:space-between; gap:6px; font-size:11px; padding:2px 5px;
          border-radius:4px; margin-top:2px; background:var(--panel2); border-left:3px solid transparent; }
  .chip.raise { border-left-color:var(--raise); } .chip.jam { border-left-color:var(--jam); }
  .chip.call { border-left-color:var(--call); } .chip.fold { border-left-color:var(--fold); }
  .chip.sel { color:var(--accent); font-weight:700; background:rgba(255,166,87,.16); }
  .chip.off { opacity:.4; }
  .cn { font-size:10px; color:var(--dim); margin-top:3px; }
  .warn { font-size:11px; color:var(--accent); border:1px solid rgba(255,166,87,.4); border-radius:7px;
          padding:5px 8px; margin-bottom:6px; }
  input[type=range] { -webkit-appearance:none; appearance:none; width:100%; height:4px;
                      border-radius:2px; background:var(--border); margin:10px 0 2px; }
  input[type=range]::-webkit-slider-thumb { -webkit-appearance:none; width:22px; height:22px;
                      border-radius:50%; background:var(--accent); }
  .ticks { position:relative; height:13px; margin:0 11px; }
  .ticks span { position:absolute; transform:translateX(-50%); font-size:9.5px; color:var(--dim); }
  .ticks span.on { color:var(--accent); font-weight:700; }
  .ticks span.miss { opacity:.35; }
  .grid { display:grid; grid-template-columns:repeat(13,1fr); gap:1.5px; margin-top:8px; }
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
  .missing { margin-top:10px; padding:26px 12px; text-align:center; color:var(--dim); font-size:13px;
             background:var(--panel); border:1px solid var(--border); border-radius:8px; }
  .legend { display:flex; gap:10px; flex-wrap:wrap; margin-top:9px; color:var(--dim); font-size:11px; }
  .legend i { display:inline-block; width:9px; height:9px; border-radius:2px; margin-right:3px; }
  .detail { margin-top:8px; padding:8px 10px; background:var(--panel); border:1px solid var(--border);
            border-radius:8px; font-size:12.5px; min-height:36px; }
  .detail b { color:var(--accent); }
  .src { margin-top:7px; color:var(--dim); font-size:10.5px; }
  @media (min-width:560px) { .c { font-size:13px; } body { max-width:640px; margin:0 auto; } }
</style>
</head>
<body>
<div class="max" id="max"></div>
<div id="warn"></div>
<div class="bar" id="bar"></div>
<h1><b id="title">—</b><span id="sum"></span></h1>
<input type="range" id="sl" min="0" max="0" value="0" step="1">
<div class="ticks" id="ticks"></div>
<div id="body"></div>
<div class="legend" id="legend"></div>
<div class="detail" id="detail">칸을 누르면 빈도가 나옵니다.</div>
<div class="src" id="src"></div>
<script>
// 앱의 📊 레인지 차트와 같은 조작: 인원 버튼(기본 7맥스, 앞자리부터 뺀다) · 위쪽 액션 카드로
// (자리, 상대)를 고른다 · 스택 슬라이더는 카드를 옮겨도 그대로 둔다. 데이터 키는 '자리|상대'.
const DATA = __DATA__;
const ALL = ['UTG', 'UTG1', 'LJ', 'HJ', 'CO', 'BTN', 'SB', 'BB'];
const RANKS = 'AKQJT98765432'.split('');
const AXIS = [...new Set(Object.values(DATA).flatMap(l => l.map(x => x.bb)))].sort((a, b) => a - b);
let max = loadMax(), pos = null, vs = '', bb = null, sel = null;

function loadMax() {
  try { const n = +localStorage.getItem('rc_max'); return [3, 4, 5, 6, 7, 8].includes(n) ? n : 7; }
  catch (e) { return 7; }
}
const seats = () => ALL.slice(8 - max);
const maxName = n => n >= 6 ? n + '맥스' : n + '명';
const list = (p, v) => DATA[p + '|' + v] || [];
const item = (p, v) => list(p, v).find(x => x.bb === bb);
// 'UTG' 레이즈 / 'UTG-allin' 올인 / 'SB-limp' 림프 — 앱의 ranges.vs_parts와 같은 규칙
function parts(v) {
  if (!v) return ['', null];
  if (v.endsWith('-allin')) return [v.slice(0, -6), 'allin'];
  if (v.endsWith('-limp')) return [v.slice(0, -5), 'limp'];
  return [v, 'raise'];
}
const KIND = {raise: '', allin: ' 올인', limp: ' 림프'};
const spot = (p, v) => { const [o, k] = parts(v); return o ? `${p} vs ${o}${KIND[k]}` : p; };

function setMax(n) {
  max = n; try { localStorage.setItem('rc_max', String(n)); } catch (e) {}
  render();
}
function go(p, v) { pos = p; vs = v; sel = null; render(); }

function barHtml(cur) {
  const order = seats(), last = order.length - 1;
  const [opName, opKind] = parts(vs);
  const ci = order.indexOf(pos), op = opName ? order.indexOf(opName) : -1;
  const has = (p, v) => !!item(p, v);
  const jams = p => { const x = item(p, ''); return !!(x && x.jam > 0.05); };
  const chip = (label, cls, o = {}) =>
    `<span class="chip ${cls}${o.sel ? ' sel' : ''}${o.off ? ' off' : ''}"${o.click && !o.off
      ? ` onclick="event.stopPropagation();${o.click}"` : ''}>${label}${o.pct == null ? '' : `<b>${o.pct}%</b>`}</span>`;
  const g = (p, v) => `go('${p}','${v}')`;
  return order.map((p, i) => {
    let cls = '', chips = '', target = null, note = '';
    const next = i < last ? order[i + 1] : null;
    const openChips = (s, pc = {}) =>
      chip('레이즈', 'raise', {sel: s === 'raise', pct: pc.raise, click: s === 'raise' ? '' : g(next, p)})
      + (s === 'allin' || pc.jam != null || jams(p)
          ? chip('올인', 'jam', {sel: s === 'allin', pct: pc.jam, click: s === 'allin' ? '' : g(next, p + '-allin')}) : '');
    if (i < ci) {
      cls = 'done';
      const after = op >= 0 && i > op;
      target = after ? [p, vs] : [p, ''];
      if (i === op) {
        chips = openChips(opKind === 'limp' ? null : opKind)
          + (opKind === 'limp' ? chip('림프', 'call', {sel: true}) : '')
          + chip('폴드', 'fold', {click: next && next !== 'BB' ? g(next, '') : '', off: next === 'BB'});
      } else if (after) {
        chips = chip(opKind === 'allin' ? '콜' : '3벳', opKind === 'allin' ? 'call' : 'raise', {off: true})
          + chip('폴드', 'fold', {sel: true});
      } else chips = openChips(null) + chip('폴드', 'fold', {sel: true});
    } else if (i === ci) {
      cls = 'cur';
      const pct = cur ? cur.pct : null, jam = cur ? cur.jam : 0, call = cur ? cur.call : 0;
      const r = x => Math.round(x);
      const fold = (click, off) => chip(opKind === 'limp' ? '체크' : '폴드', 'fold', {pct: cur ? r(100 - pct) : null, click, off});
      if (!vs) {
        chips = (next ? openChips(null, {raise: cur ? r(pct - jam - call) : null, ...(jam > 0.05 ? {jam: r(jam)} : {})})
                      : chip('레이즈', 'raise', {off: true}))
          + (call > 0.05 ? chip('림프', 'call', {pct: r(call), click: p === 'SB' ? g('BB', 'SB-limp') : '', off: p !== 'SB'}) : '')
          + fold(next && next !== 'BB' ? g(next, '') : '', next === 'BB');
      } else if (opKind === 'limp') {
        chips = chip('레이즈', 'raise', {pct: cur ? r(pct - jam) : null, off: true})
          + (jam > 0.05 ? chip('올인', 'jam', {pct: r(jam), off: true}) : '') + fold('', true);
      } else if (opKind === 'allin') {
        chips = chip('콜', 'call', {pct: cur ? r(call) : null, off: true}) + fold(next ? g(next, vs) : '', !next);
      } else {
        chips = chip('3벳', 'raise', {pct: cur ? r(pct - jam - call) : null, off: true})
          + (jam > 0.05 ? chip('올인', 'jam', {pct: r(jam), off: true}) : '')
          + chip('콜', 'call', {pct: cur ? r(call) : null, off: true}) + fold(next ? g(next, vs) : '', !next);
      }
      if (!cur) note = '<div class="cn">차트 없음</div>';
    } else {
      cls = 'later';
      if (vs) target = [p, vs]; else if (p !== 'BB') target = [p, ''];
      if (!target) note = '<div class="cn">워크</div>';
    }
    if (target && !has(target[0], target[1])) { cls += ' none'; if (cls.includes('later')) note = '<div class="cn">없음</div>'; }
    const tag = i === op ? `<i>${{raise: '오픈', allin: '올인', limp: '림프'}[opKind]}</i>` : '';
    const fmt = i === 0 && max < 8 && p !== 'BTN' ? `<u>${maxName(max)} UTG</u>` : '';
    return `<div class="card ${cls}"${target ? ` onclick="${g(target[0], target[1])}"` : ''}>
      <div class="pn">${p}${tag}${fmt}</div>${chips}${note}</div>`;
  }).join('');
}

function combo(i, j) {
  const hi = RANKS[Math.min(i, j)], lo = RANKS[Math.max(i, j)];
  return i === j ? hi + lo : hi + lo + (i < j ? 's' : 'o');
}

function render() {
  const S = seats();
  if (!pos || !S.includes(pos) || (vs && !S.includes(parts(vs)[0]))) { pos = S[0]; vs = ''; }
  if (bb === null || !AXIS.includes(bb)) bb = (list(pos, '')[0] || {}).bb ?? AXIS[0];
  const cur = item(pos, vs), [, kind] = parts(vs);
  document.getElementById('max').innerHTML = [8, 7, 6, 5, 4, 3].map(n =>
    `<button class="${n === max ? 'on' : ''}" onclick="setMax(${n})">${maxName(n)}</button>`).join('');
  document.getElementById('warn').innerHTML = max <= 5
    ? '<div class="warn">⚠️ 칩EV 차트입니다 — 파이널 테이블·버블에선 ICM 때문에 특히 올인 콜이 훨씬 타이트해야 합니다.</div>' : '';
  const bar = document.getElementById('bar');
  bar.innerHTML = barHtml(cur);
  // 폰 너비에선 카드 줄이 넘친다 — 지금 차례 카드가 화면 밖에 있으면 그쪽으로 굴린다
  const on = bar.querySelector('.card.cur');
  if (on && (on.offsetLeft < bar.scrollLeft || on.offsetLeft + on.offsetWidth > bar.scrollLeft + bar.clientWidth))
    bar.scrollLeft = on.offsetLeft + on.offsetWidth - bar.clientWidth + 4;
  document.getElementById('title').textContent = spot(pos, vs) + ' · ' + bb + 'bb';
  document.getElementById('sum').textContent = cur
    ? '액션 ' + cur.pct + '%' + (cur.call ? ' · 콜 ' + cur.call + '%' : '') + (cur.jam ? ' · 올인 ' + cur.jam + '%' : '') : '';
  const sl = document.getElementById('sl');
  sl.max = AXIS.length - 1; sl.value = AXIS.indexOf(bb);
  document.getElementById('ticks').innerHTML = AXIS.length < 2 ? '' : AXIS.map((b, i) =>
    `<span style="left:${i / (AXIS.length - 1) * 100}%" class="${b === bb ? 'on' : ''}${
      list(pos, vs).some(x => x.bb === b) ? '' : ' miss'}">${b}</span>`).join('');
  const body = document.getElementById('body');
  if (!cur) {
    body.innerHTML = `<div class="missing">${spot(pos, vs)} · ${bb}bb 차트가 아직 없습니다</div>`;
    document.getElementById('legend').innerHTML = '';
    document.getElementById('src').textContent = '';
    document.getElementById('detail').textContent = '';
    return;
  }
  let g = '<div class="grid">';
  for (let i = 0; i < 13; i++) for (let j = 0; j < 13; j++) {
    const c = combo(i, j), d = cur.cells[c] || [0, 0, 0];
    const w = d[0], jm = d[1], cl = d[2];
    g += `<div class="c${i === j ? ' pair' : ''}${sel === c ? ' sel' : ''}" onclick="tap('${c}')">` +
         (w - cl > 0 ? `<i class="r" style="width:${w - cl}%"></i>` : '') +
         (jm > 0 ? `<i class="j" style="width:${jm}%"></i>` : '') +
         (cl > 0 ? `<i class="k" style="left:${w - cl}%;width:${cl}%"></i>` : '') +
         `<span>${c}</span></div>`;
  }
  body.innerHTML = g + '</div>';
  const raiseName = kind === 'raise' ? '3벳' : '레이즈';
  document.getElementById('legend').innerHTML =
    (cur.jam ? '<span><i style="background:var(--jam)"></i>올인</span>' : '') +
    (kind === 'allin' ? '' : `<span><i style="background:var(--raise)"></i>${raiseName}</span>`) +
    (cur.call ? `<span><i style="background:var(--call)"></i>${vs ? '콜' : '림프'}</span>` : '') +
    `<span><i style="background:var(--fold)"></i>${kind === 'limp' ? '체크' : '폴드'}</span>`;
  document.getElementById('src').textContent = cur.src ? '출처: ' + cur.src : '';
  if (sel) tap(sel, true); else document.getElementById('detail').textContent = '칸을 누르면 빈도가 나옵니다.';
}

function tap(c, keep) {
  const cur = item(pos, vs); if (!cur) return;
  const d = cur.cells[c] || [0, 0, 0], [, kind] = parts(vs);
  sel = c;
  const P = [], raise = d[0] - d[1] - d[2];
  if (raise > 0) P.push((kind === 'raise' ? '3벳 ' : '레이즈 ') + raise + '%');
  if (d[1] > 0) P.push('올인 ' + d[1] + '%');
  if (d[2] > 0) P.push((vs ? '콜 ' : '림프 ') + d[2] + '%');
  if (d[0] < 100) P.push((kind === 'limp' ? '체크 ' : '폴드 ') + (100 - d[0]) + '%');
  let t = `<b>${c}</b> — ` + P.join(' · ');
  const h = cur.hero && cur.hero[c];
  if (h) t += `<br><span style="color:var(--dim)">실전: ${h[1]}회 중 ${h[0]}회 ${
    !vs ? '오픈' : kind === 'allin' ? '콜' : kind === 'limp' ? '레이즈' : '방어'} (${Math.round(h[0] / h[1] * 100)}%)</span>`;
  document.getElementById('detail').innerHTML = t;
  if (!keep) render();
}
document.getElementById('sl').addEventListener('input', e => { bb = AXIS[+e.target.value]; sel = null; render(); });
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
    print(f"   스팟 {len(data)}개 · 차트 {n}장" + (" · 내 실전 기록 포함" if a.hero else ""))
    print("   폰으로 옮겨 브라우저로 열고 '홈 화면에 추가'하면 앱처럼 쓸 수 있습니다 (오프라인 동작).")


if __name__ == "__main__":
    main()
