#!/usr/bin/env python3
"""CoinPoker 핸드 히스토리 컨버터 — 로컬 웹 GUI.

사용법:
    python3 gui.py              # 서버 시작 + 브라우저 자동 오픈
    python3 gui.py hands.txt    # 파일을 미리 로드한 상태로 시작
    python3 gui.py --port 9000
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import store


# ---------------------------------------------------------------------------
# AI 분석 백엔드 (교체 가능한 구조)
#
# 새 백엔드 추가 방법: name / available() / analyze(hand_md) 를 가진 클래스를
# 만들고 BACKENDS 에 등록하면 됨. --ai 플래그 또는 auto 감지로 선택.
# ---------------------------------------------------------------------------

ANALYSIS_SYSTEM_PROMPT = """\
당신은 NLH 토너먼트 전문 포커 코치입니다. 제공되는 핸드 히스토리에서 Hero의 플레이를 분석하세요.

규칙:
- 각 스트리트(프리플랍/플랍/턴/리버)별로 Hero의 결정을 평가하세요. Hero가 참여하지 않은 스트리트는 건너뜁니다.
- 포지션, 스택 깊이(bb), 팟 오즈, 상대의 예상 레인지를 근거로 제시하세요.
- 토너먼트이므로 스택 보존 관점도 고려하세요.
- 결과론으로 평가하지 마세요. 결정 시점에 알 수 있던 정보만으로 판단하세요.
- 각 스트리트 평가는 [좋음/무난/의문/실수] 중 하나로 시작하세요.
- 마지막에 "## 총평"으로 핵심 교훈을 1~3개 정리하세요.
- 한국어, 마크다운 형식(## 스트리트명)으로, 간결하게 작성하세요.
"""


class ClaudeCLIBackend:
    """Claude Code CLI 헤드리스 모드(claude -p) 사용. 별도 API 키 불필요."""

    name = "claude-cli"

    def available(self):
        return shutil.which("claude") is not None

    def analyze_stream(self, hand_md):
        """분석 텍스트를 생성되는 대로 chunk 단위로 yield."""
        prompt = ANALYSIS_SYSTEM_PROMPT + "\n다음 핸드를 분석하세요:\n\n" + hand_md
        # shutil.which로 절대경로 해석(윈도우 PATH 대응), encoding 고정(윈도우 cp949 방지)
        claude_bin = shutil.which("claude") or "claude"
        proc = subprocess.Popen(
            [claude_bin, "-p", "--output-format", "stream-json",
             "--include-partial-messages", "--verbose"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
        )
        proc.stdin.write(prompt)
        proc.stdin.close()
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                # stream_event 안의 text_delta 만 추출 (thinking 델타는 제외)
                if obj.get("type") == "stream_event":
                    ev = obj.get("event", {})
                    if ev.get("type") == "content_block_delta":
                        delta = ev.get("delta", {})
                        if delta.get("type") == "text_delta" and delta.get("text"):
                            yield delta["text"]
            proc.wait(timeout=30)
            if proc.returncode != 0:
                err = proc.stderr.read().strip()
                raise RuntimeError(err or "claude CLI 실행 실패")
        finally:
            if proc.poll() is None:
                proc.kill()


class AnthropicAPIBackend:
    """Anthropic API 직접 호출. anthropic SDK + ANTHROPIC_API_KEY 필요."""

    name = "anthropic-api"

    def available(self):
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False
        return bool(os.environ.get("ANTHROPIC_API_KEY")
                    or os.environ.get("ANTHROPIC_AUTH_TOKEN"))

    def analyze_stream(self, hand_md):
        """분석 텍스트를 생성되는 대로 chunk 단위로 yield."""
        import anthropic
        client = anthropic.Anthropic()
        with client.messages.stream(
            model="claude-opus-4-8",
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=ANALYSIS_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": "다음 핸드를 분석하세요:\n\n" + hand_md}],
        ) as stream:
            yield from stream.text_stream


BACKENDS = [AnthropicAPIBackend(), ClaudeCLIBackend()]  # auto 우선순위 순
AI_BACKEND = None  # main()에서 결정


def select_backend(choice):
    if choice == "api":
        return BACKENDS[0]
    if choice == "cli":
        return BACKENDS[1]
    for b in BACKENDS:  # auto: 사용 가능한 첫 백엔드
        if b.available():
            return b
    return None


# ---------------------------------------------------------------------------
# HTTP 서버
# ---------------------------------------------------------------------------

DB = None        # main()에서 로드되는 핸드 DB
DB_PATH = None

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>Hand History Converter</title>
<style>
  :root {
    --bg: #14171c; --panel: #1d2128; --panel2: #242a33; --border: #313845;
    --text: #d8dee8; --dim: #8a93a3; --accent: #4da3ff; --green: #3fbf6f; --red: #e0556a;
    --gold: #e8b84f;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text);
         font: 14px/1.55 -apple-system, "Apple SD Gothic Neo", "Noto Sans KR", sans-serif; }
  header { display: flex; align-items: center; gap: 14px; padding: 12px 18px;
           background: var(--panel); border-bottom: 1px solid var(--border); }
  header h1 { font-size: 16px; font-weight: 600; }
  header .spacer { flex: 1; }
  label.small { color: var(--dim); font-size: 12px; }
  input[type=text] { background: var(--panel2); border: 1px solid var(--border);
                     color: var(--text); border-radius: 6px; padding: 5px 9px; width: 110px; }
  button { background: var(--panel2); border: 1px solid var(--border); color: var(--text);
           border-radius: 6px; padding: 6px 13px; cursor: pointer; font-size: 13px; }
  button:hover { border-color: var(--accent); color: var(--accent); }
  button.primary { background: var(--accent); border-color: var(--accent); color: #0c1117; font-weight: 600; }
  button.primary:hover { filter: brightness(1.1); color: #0c1117; }

  #drop { margin: 60px auto; max-width: 620px; border: 2px dashed var(--border);
          border-radius: 14px; padding: 70px 40px; text-align: center; color: var(--dim);
          transition: border-color .15s, background .15s; cursor: pointer; }
  #drop.over { border-color: var(--accent); background: rgba(77,163,255,.06); }
  #drop strong { color: var(--text); font-size: 16px; display: block; margin-bottom: 8px; }

  #layout { display: none; height: calc(100vh - 53px); }
  #layout.active { display: flex; }
  #sidebar { width: 320px; min-width: 320px; overflow-y: auto;
             background: var(--panel); border-right: 1px solid var(--border); padding: 12px; }
  .tourney { padding: 11px 12px; border: 1px solid var(--border); border-radius: 9px;
             margin-bottom: 9px; cursor: pointer; transition: border-color .1s; }
  .tourney:hover { border-color: var(--accent); }
  .tourney.sel { border-color: var(--accent); background: rgba(77,163,255,.08); }
  .tourney .tname { font-weight: 600; margin-bottom: 3px; }
  .tourney .tmeta { color: var(--dim); font-size: 12px; }

  #main { flex: 1; overflow-y: auto; padding: 18px 24px; }
  #mainhead { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; flex-wrap: wrap; }
  #mainhead h2 { font-size: 17px; flex: 1; min-width: 200px; }

  .hand { border: 1px solid var(--border); border-radius: 10px; margin-bottom: 10px;
          background: var(--panel); overflow: hidden; }
  .hand-head { display: flex; align-items: center; gap: 12px; padding: 10px 14px; cursor: pointer; }
  .hand-head:hover { background: var(--panel2); }
  .hand-head .hid { color: var(--dim); font-size: 12px; width: 105px; }
  .hand-head .pos { background: var(--panel2); border: 1px solid var(--border);
                    border-radius: 5px; padding: 1px 7px; font-size: 12px; width: 52px; text-align: center; }
  .hand-head .cards { font-weight: 700; font-size: 15px; width: 84px; letter-spacing: 1px; }
  .hand-head .tags { flex: 1; color: var(--dim); font-size: 12px; }
  .hand-head .net { font-weight: 700; font-size: 13px; width: 110px; text-align: right; }
  .pos-badge { color: var(--gold); }
  .net.win { color: var(--green); } .net.lose { color: var(--red); }
  .hand-body { display: none; border-top: 1px solid var(--border);
               padding: 14px 18px; background: #181c22; }
  .hand.open .hand-body { display: block; }

  .hand-body h2 { font-size: 15px; margin-bottom: 4px; color: var(--gold); }
  .hand-body p { margin: 2px 0; }
  .hand-body ul { list-style: none; margin: 2px 0 10px 6px; }
  .hand-body li { padding: 1px 0; }
  .hand-body strong { color: #fff; }
  .hand-body .sect { margin-top: 10px; font-weight: 700; color: var(--accent); }
  .decision { background: rgba(232,184,79,.12); border-left: 3px solid var(--gold);
              padding-left: 8px; border-radius: 3px; }

  .suit-s { color: #c9d3e0; } .suit-h { color: #ff6b7d; }
  .suit-d { color: #58a6ff; } .suit-c { color: #56d364; }

  .ai-box { margin-top: 14px; border-top: 1px dashed var(--border); padding-top: 12px; }
  .ai-result { background: rgba(77,163,255,.05); border: 1px solid rgba(77,163,255,.25);
               border-radius: 9px; padding: 12px 16px; margin-top: 8px; }
  .ai-result h2 { color: var(--accent) !important; font-size: 14px; margin-top: 8px; }
  .ai-loading { color: var(--dim); padding: 8px 2px; }
  .ai-loading::after { content: ''; animation: dots 1.4s steps(4,end) infinite; }
  @keyframes dots { 0% {content:'';} 25% {content:'.';} 50% {content:'..';} 75% {content:'...';} }
  .ai-error { color: var(--red); padding: 6px 2px; font-size: 13px; }
  .ai-cursor { color: var(--accent); animation: blink 1s steps(2,start) infinite; }
  @keyframes blink { to { visibility: hidden; } }
  .ai-meta { color: var(--dim); font-size: 11px; margin-top: 6px; text-align: right; }

  .toast { position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
           background: var(--accent); color: #0c1117; font-weight: 600;
           padding: 9px 20px; border-radius: 8px; opacity: 0; transition: opacity .25s; }
  .toast.show { opacity: 1; }
</style>
</head>
<body>
<header>
  <h1>🃏 Hand History Converter</h1>
  <span class="spacer"></span>
  <label class="small">Hero 이름 <input type="text" id="hero" value="Hero"></label>
  <button id="btnOpen">파일 열기</button>
  <input type="file" id="file" accept=".txt,.log" multiple style="display:none">
</header>

<div id="drop">
  <strong>핸드 히스토리 파일을 여기에 드래그</strong>
  클릭해서 파일을 선택할 수도 있어요 (.txt, 여러 개 가능)
</div>

<div id="layout">
  <div id="sidebar"></div>
  <div id="main">
    <div id="mainhead"></div>
    <div id="hands"></div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
let DATA = null, SEL = 0, HIDE_FOLDS = false;

const $ = s => document.querySelector(s);

function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 1800);
}

function esc(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// 카드 토큰(As, Td, 9c...)에 무늬 색 입히기
function colorCards(html) {
  return html.replace(/\b([2-9TJQKA])([shdc])\b/g, (m, r, s) => {
    const glyph = {s:'♠', h:'♥', d:'♦', c:'♣'}[s];
    return `<span class="suit-${s}">${r}${glyph}</span>`;
  });
}

// 핸드 상세 표시용: 상단 헤더 2줄(## Hand #... / NLH | Blinds ...) 제거
// 복사/다운로드용 마크다운에는 유지됨 (AI에 단독으로 줄 때 필요한 컨텍스트)
function stripHeader(md) {
  return md.replace(/^## [^\n]*\n[^\n]*\n+/, '');
}

// 변환 마크다운 → 간단 HTML
function mdToHtml(md) {
  const lines = md.split('\n');
  let out = [], inList = false;
  for (let line of lines) {
    let h = esc(line);
    h = h.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    if (line.startsWith('## ')) { if(inList){out.push('</ul>');inList=false;}
      out.push('<h2>' + h.slice(3) + '</h2>'); continue; }
    if (line.startsWith('- ')) {
      if (!inList) { out.push('<ul>'); inList = true; }
      const cls = line.includes('HERO DECISION') ? ' class="decision"' : '';
      out.push(`<li${cls}>` + h.slice(2) + '</li>'); continue;
    }
    if (inList) { out.push('</ul>'); inList = false; }
    if (line.trim() === '') continue;
    if (/^\*\*(PREFLOP|FLOP|TURN|RIVER|SHOWDOWN|RESULT|Players:)/.test(line))
      out.push('<p class="sect">' + h + '</p>');
    else out.push('<p>' + h + '</p>');
  }
  if (inList) out.push('</ul>');
  return colorCards(out.join('\n'));
}

function cardsHtml(cards) {
  return colorCards(esc(cards.join(' '))) || '<span style="color:var(--dim)">—</span>';
}

function netHtml(net, netBb) {
  const cls = net >= 0 ? 'win' : 'lose';
  const sign = net >= 0 ? '+' : '';
  return `<span class="net ${cls}">${sign}${net.toLocaleString()} (${sign}${netBb}bb)</span>`;
}

function renderSidebar() {
  $('#sidebar').innerHTML = DATA.tournaments.map((t, i) => `
    <div class="tourney ${i===SEL?'sel':''}" onclick="selectTourney(${i})">
      <div class="tname">${esc(t.name)}</div>
      <div class="tmeta">#${t.id} · 핸드 ${t.hand_count}개<br>${esc(t.start.slice(0,16))} ~ ${esc(t.end.slice(11,16))}</div>
    </div>`).join('');
}

// 현재 표시 대상 핸드 (필터 적용)
function visibleHands() {
  const t = DATA.tournaments[SEL];
  return HIDE_FOLDS ? t.hands.filter(h => !h.no_action_fold) : t.hands;
}

function toggleFolds() { HIDE_FOLDS = !HIDE_FOLDS; renderMain(); }

function renderMain() {
  const t = DATA.tournaments[SEL];
  const hands = visibleHands();
  const countLabel = HIDE_FOLDS
    ? `${hands.length}핸드 표시 (프리폴드 ${t.hand_count - hands.length}개 숨김)`
    : `${t.hand_count}핸드`;
  $('#mainhead').innerHTML = `
    <h2>${esc(t.name)} <span style="color:var(--dim);font-size:13px">#${t.id} · ${countLabel}</span></h2>
    <button onclick="toggleFolds()" class="${HIDE_FOLDS ? 'primary' : ''}">${HIDE_FOLDS ? '✓ ' : ''}프리폴드 숨기기</button>
    <button onclick="toggleAll(true)">모두 펼치기</button>
    <button onclick="toggleAll(false)">모두 접기</button>
    <button onclick="copyMd()">📋 마크다운 복사</button>
    <button class="primary" onclick="downloadMd()">⬇ .md 다운로드</button>`;
  $('#hands').innerHTML = hands.map((h, i) => {
    const tags = [];
    if (!h.vpip) tags.push('fold');
    if (h.showdown) tags.push('showdown');
    return `
    <div class="hand" id="hand${i}">
      <div class="hand-head" onclick="document.getElementById('hand${i}').classList.toggle('open')">
        <span class="hid">#${h.hand_id.slice(-6)} ${esc(h.datetime.slice(11,19))}</span>
        <span class="pos pos-badge">${h.hero_pos || '?'}</span>
        <span class="cards">${cardsHtml(h.hero_cards)}</span>
        <span class="tags">${h.blinds} · ${h.players}p${tags.length ? ' · ' + tags.join(' · ') : ''}</span>
        ${netHtml(h.net, h.net_bb)}
      </div>
      <div class="hand-body">
        ${mdToHtml(stripHeader(h.markdown))}
        <div class="ai-box" id="ai-${h.hand_id}">${aiBoxHtml(h.hand_id)}</div>
      </div>
    </div>`;
  }).join('');
}

// --- AI 분석 (스트리밍) ---
const AI_CACHE = {};  // hand_id -> {status: 'loading'|'streaming'|'done'|'error', text, backend}

function aiBoxHtml(handId) {
  const c = AI_CACHE[handId];
  if (!c) return `<button onclick="analyzeHand('${handId}')">🤖 AI 분석</button>`;
  if (c.status === 'loading') return `<div class="ai-loading">AI가 핸드를 분석하는 중</div>`;
  if (c.status === 'error') return `
    <div class="ai-error">분석 실패: ${esc(c.text)}</div>
    <button onclick="analyzeHand('${handId}')">다시 시도</button>`;
  const streaming = c.status === 'streaming';
  return `
    <div class="ai-result">${mdToHtml(c.text)}${streaming ? '<span class="ai-cursor">▍</span>' : ''}
      ${streaming ? '' : `<div class="ai-meta">분석: ${esc(c.backend || 'AI')} · <a href="#" style="color:var(--dim)"
        onclick="event.preventDefault(); analyzeHand('${handId}')">다시 분석</a></div>`}
    </div>`;
}

function renderAIBox(handId) {
  const el = document.getElementById('ai-' + handId);
  if (el) el.innerHTML = aiBoxHtml(handId);
}

async function analyzeHand(handId) {
  let hand = null;
  for (const t of DATA.tournaments) {
    hand = t.hands.find(h => h.hand_id === handId);
    if (hand) break;
  }
  if (!hand) return;
  AI_CACHE[handId] = {status: 'loading'};
  renderAIBox(handId);
  try {
    const res = await fetch('/api/analyze', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({markdown: hand.markdown, hand_id: handId}),
    });
    if (!res.ok) {
      const data = await res.json();
      AI_CACHE[handId] = {status: 'error', text: data.error || ('HTTP ' + res.status)};
      renderAIBox(handId);
      return;
    }
    const backend = res.headers.get('X-AI-Backend') || 'AI';
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let text = '';
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      text += decoder.decode(value, {stream: true});
      AI_CACHE[handId] = {status: 'streaming', text, backend};
      renderAIBox(handId);
    }
    text += decoder.decode();
    if (!text.trim()) {
      AI_CACHE[handId] = {status: 'error', text: 'AI가 빈 응답을 반환했습니다.'};
    } else {
      AI_CACHE[handId] = {status: 'done', text, backend};
    }
  } catch (e) {
    AI_CACHE[handId] = {status: 'error', text: String(e)};
  }
  renderAIBox(handId);
}

function selectTourney(i) { SEL = i; renderSidebar(); renderMain(); $('#main').scrollTop = 0; }
function toggleAll(open) {
  document.querySelectorAll('.hand').forEach(el => el.classList.toggle('open', open));
}

function tourneyMd() {
  // 필터가 켜져 있으면 표시 중인 핸드만 복사/다운로드
  return visibleHands().map(h => h.markdown).join('\n---\n\n');
}
function copyMd() {
  navigator.clipboard.writeText(tourneyMd()).then(() => toast('복사 완료 — AI에게 붙여넣으세요'));
}
function downloadMd() {
  const t = DATA.tournaments[SEL];
  const blob = new Blob([tourneyMd()], {type: 'text/markdown'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `tournament_${t.id}.md`;
  a.click();
}

function applyData(data) {
  DATA = data; SEL = 0;
  // DB에 저장된 AI 분석 결과를 캐시에 복원
  for (const t of DATA.tournaments)
    for (const h of t.hands)
      if (h.analysis && !AI_CACHE[h.hand_id])
        AI_CACHE[h.hand_id] = {status: 'done', text: h.analysis, backend: '저장됨'};
  const params = new URLSearchParams(location.search);
  HIDE_FOLDS = params.has('hidefolds');
  $('#drop').style.display = 'none';
  $('#layout').classList.add('active');
  renderSidebar(); renderMain();
  if (params.has('expand')) toggleAll(true);
}

async function importText(text) {
  const hero = $('#hero').value.trim() || 'Hero';
  const res = await fetch('/api/import?hero=' + encodeURIComponent(hero), {
    method: 'POST', body: text,
  });
  const data = await res.json();
  if (data.error) { toast(data.error); return; }
  applyData(data);
  toast(`신규 ${data.added}개 추가 · 기존 ${data.skipped}개 스킵`);
}

async function loadFiles(files) {
  let text = '';
  for (const f of files) text += await f.text() + '\n';
  importText(text);
}

// 드래그&드롭 / 파일 선택
const drop = $('#drop');
drop.onclick = () => $('#file').click();
$('#btnOpen').onclick = () => $('#file').click();
$('#file').onchange = e => loadFiles(e.target.files);
['dragover','dragenter'].forEach(ev => document.body.addEventListener(ev, e => {
  e.preventDefault(); drop.classList.add('over');
}));
['dragleave','drop'].forEach(ev => document.body.addEventListener(ev, e => {
  e.preventDefault(); drop.classList.remove('over');
}));
document.body.addEventListener('drop', e => loadFiles(e.dataTransfer.files));

// 저장된 DB가 있으면 시작하자마자 표시
fetch('/api/db').then(r => r.json()).then(d => {
  if (d.tournaments && d.tournaments.length) applyData(d);
});
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, body, ctype="text/html; charset=utf-8", code=200):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(INDEX_HTML)
        elif path == "/api/db":
            resp = store.tournaments_response(DB)
            self._send(json.dumps(resp, ensure_ascii=False), "application/json; charset=utf-8")
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path.startswith("/api/import"):
            hero = "Hero"
            if "hero=" in self.path:
                from urllib.parse import parse_qs, urlparse
                qs = parse_qs(urlparse(self.path).query)
                hero = qs.get("hero", ["Hero"])[0]
            length = int(self.headers.get("Content-Length", 0))
            text = self.rfile.read(length).decode("utf-8", errors="replace")
            added, skipped = store.import_text(DB, text, hero=hero)
            if added:
                store.save_db(DB_PATH, DB)
            if not added and not skipped:
                resp = {"error": "핸드를 찾지 못했습니다. 'CoinPoker Hand #' 로 시작하는 로그인지 확인하세요."}
            else:
                resp = {"added": added, "skipped": skipped}
                resp.update(store.tournaments_response(DB))
            self._send(json.dumps(resp, ensure_ascii=False), "application/json; charset=utf-8")
        elif self.path == "/api/analyze":
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                hand_md = body.get("markdown", "")
                if not hand_md.strip():
                    raise ValueError("분석할 핸드 데이터가 없습니다.")
                if AI_BACKEND is None:
                    raise RuntimeError(
                        "사용 가능한 AI 백엔드가 없습니다. claude CLI 설치 또는 "
                        "ANTHROPIC_API_KEY 설정 후 다시 실행하세요.")
            except Exception as e:
                self._send(json.dumps({"error": str(e)}, ensure_ascii=False),
                           "application/json; charset=utf-8", code=400)
                return
            # 스트리밍 응답: 생성되는 텍스트를 즉시 브라우저로 전달
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-AI-Backend", AI_BACKEND.name)
            self.end_headers()
            full = []
            ok = True
            try:
                for chunk in AI_BACKEND.analyze_stream(hand_md):
                    full.append(chunk)
                    self.wfile.write(chunk.encode("utf-8"))
                    self.wfile.flush()
            except BrokenPipeError:
                ok = False  # 클라이언트가 연결을 끊음 — 불완전 분석은 저장 안 함
            except Exception as e:
                ok = False
                try:
                    msg = f"\n\n> ⚠️ 분석 중 오류: {e}"
                    self.wfile.write(msg.encode("utf-8"))
                    self.wfile.flush()
                except BrokenPipeError:
                    pass
            # 완료된 분석은 DB에 영구 저장 → 재분석 불필요
            hand_id = body.get("hand_id")
            text = "".join(full).strip()
            if ok and text and hand_id in DB["hands"]:
                DB["hands"][hand_id]["analysis"] = text
                store.save_db(DB_PATH, DB)
        else:
            self.send_error(404)

    def log_message(self, *args):  # 콘솔 로그 끄기
        pass


def main():
    global DB, DB_PATH, AI_BACKEND
    ap = argparse.ArgumentParser(description="핸드 히스토리 컨버터 웹 GUI")
    ap.add_argument("input", nargs="*", help="DB에 임포트할 핸드 히스토리 파일 (선택)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="브라우저 자동 오픈 안 함")
    ap.add_argument("--ai", choices=["auto", "cli", "api"], default="auto",
                    help="AI 분석 백엔드: cli=Claude Code CLI, api=Anthropic API (기본: auto)")
    ap.add_argument("--db", default="hands_db.json", help="핸드 DB 파일 경로 (기본: hands_db.json)")
    ap.add_argument("--hero", default="Hero", help="히어로 플레이어 이름 (기본: Hero)")
    ap.add_argument("--rebuild", action="store_true",
                    help="저장된 원본으로 전체 재변환 (컨버터 개선 후 사용. AI 분석은 유지)")
    args = ap.parse_args()

    DB_PATH = args.db
    DB = store.load_db(DB_PATH)

    if args.rebuild and DB["hands"]:
        store.rebuild(DB, hero=args.hero)
        store.save_db(DB_PATH, DB)
        print(f"재변환 완료: {len(DB['hands'])}개 핸드")

    # CLI로 받은 파일은 시작 시 DB에 임포트
    for path in args.input:
        with open(path, encoding="utf-8") as f:
            added, skipped = store.import_text(DB, f.read(), hero=args.hero)
        print(f"{path}: 신규 {added}개 추가, 기존 {skipped}개 스킵")
        if added:
            store.save_db(DB_PATH, DB)

    AI_BACKEND = select_backend(args.ai)

    n_hands = len(DB["hands"])
    n_tourneys = len({r["tournament_id"] for r in DB["hands"].values()})
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"핸드 히스토리 컨버터 실행 중 → {url}  (Ctrl+C 로 종료)")
    print(f"DB: {DB_PATH} — 핸드 {n_hands}개 / 토너먼트 {n_tourneys}개")
    print(f"AI 분석 백엔드: {AI_BACKEND.name if AI_BACKEND else '없음 (분석 비활성화)'}")
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")


if __name__ == "__main__":
    main()
