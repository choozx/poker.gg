#!/usr/bin/env python3
"""CoinPoker 핸드 히스토리 컨버터 — 로컬 웹 GUI.

사용법:
    python3 gui.py              # 서버 시작 + 브라우저 자동 오픈
    python3 gui.py hands.txt    # 파일을 미리 로드한 상태로 시작
    python3 gui.py --port 9000
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import bankroll
import cloud_sync
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
- [좋음]/[무난] 평가는 한 줄로 끝내세요. [의문]/[실수]일 때만 근거와 더 나은 액션을 1~2문장 추가하세요.
- 핸드 상황을 재서술하지 마세요. 바로 평가부터 시작하세요.
- 마지막에 "## 총평"으로 핵심 교훈을 1~3개 정리하세요.
- "## 총평" 첫 줄은 반드시 "전체 평가: [좋음]" 형식으로, Hero 플레이 전체를 [좋음/무난/의문/실수] 중 하나로 평가하세요.
- 한국어, 마크다운 형식(## 스트리트명)으로, 간결하게 작성하세요.
"""


REPORT_SYSTEM_PROMPT = """\
당신은 NLH 토너먼트 전문 포커 코치입니다. 한 플레이어(Hero)의 핸드별 AI 분석 모음을 읽고 종합 리포트를 작성하세요.

형식 (정확히 준수):
## 반복되는 실수 패턴
패턴별로 (빈도 높은 순, 최대 5개):
- **패턴 제목** — 근거 핸드 번호들. 왜 EV 손실인지 1~2문장. 교정 방법 1문장.
## 잘하고 있는 점
- 1~2개, 각 한 줄
## 우선 교정 1순위
- 가장 EV 손실이 큰 패턴 하나와 구체적인 실행 지침 2~3문장

규칙:
- 반드시 핸드 번호를 인용해 근거를 제시하세요. 근거 없는 일반론 금지.
- 분석 모음에 실수가 없으면 패턴을 억지로 만들지 말고 그렇다고 쓰세요.
- 한국어, 간결하게.
"""


class ClaudeCLIBackend:
    """Claude Code CLI 헤드리스 모드(claude -p) 사용. 별도 API 키 불필요."""

    name = "claude-cli"

    def available(self):
        return shutil.which("claude") is not None

    def stream(self, system, user):
        """system+user 프롬프트로 생성 텍스트를 chunk 단위로 yield."""
        prompt = system + "\n" + user
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

    def stream(self, system, user):
        """system+user 프롬프트로 생성 텍스트를 chunk 단위로 yield."""
        import anthropic
        client = anthropic.Anthropic()
        with client.messages.stream(
            model="claude-opus-4-8",
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=system,
            messages=[{"role": "user", "content": user}],
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
DB_PATH = None   # 로컬 저장 경로 (클라우드 모드면 ~/.cache 캐시, 아니면 --db)

# --- 클라우드 동기화 (opt-in) ------------------------------------------------
# 저장(persist)될 때마다 변경을 표시하고, 잠잠해지면(디바운스) 딱 한 번 업로드한다.
# 내용이 직전 업로드와 같으면 스킵 → 과금·트래픽·API 호출 최소.
CLOUD = False                      # main()에서 cloud_sync.available()로 결정
DEBOUNCE_SEC = 8.0
_push_lock = threading.Lock()
_push_timer = None
_last_pushed_hash = None
_db_dirty = False


def _db_hash(db):
    return hashlib.sha256(json.dumps(db, ensure_ascii=False).encode("utf-8")).hexdigest()


def _do_push():
    """디바운스 만료/종료 시 실제 업로드. 내용이 직전과 같으면 스킵."""
    global _last_pushed_hash, _db_dirty
    with _push_lock:
        if not _db_dirty:
            return
        h = _db_hash(DB)
        if h == _last_pushed_hash:        # 저장은 일어났지만 내용 동일(예: --rebuild)
            _db_dirty = False
            return
        try:
            n = cloud_sync.push(DB)
            _last_pushed_hash = h
            _db_dirty = False
            print(f"☁️  클라우드 동기화 완료 ({n / 1e6:.1f} MB)")
        except cloud_sync.CloudError as e:
            print(f"⚠️  클라우드 업로드 실패 (로컬 캐시는 보존됨): {e}")


def _schedule_push():
    """변경 발생 시 호출 — 디바운스 타이머 리셋. 연속 변경은 하나로 묶인다."""
    global _push_timer, _db_dirty
    _db_dirty = True
    if _push_timer is not None:
        _push_timer.cancel()
    _push_timer = threading.Timer(DEBOUNCE_SEC, _do_push)
    _push_timer.daemon = True
    _push_timer.start()


def _flush_push():
    """종료 시 — 대기 중 업로드를 즉시 마무리."""
    global _push_timer
    if _push_timer is not None:
        _push_timer.cancel()
        _push_timer = None
    _do_push()


def persist(db):
    """DB를 로컬에 원자적으로 저장하고, 클라우드 모드면 디바운스 push를 예약한다.
    저장 지점은 모두 이 함수를 거친다 (store.save_db 직접 호출 대신)."""
    store.save_db(DB_PATH, db)
    if CLOUD:
        _schedule_push()


INDEX_HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>poker.gg</title>
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
  .tourney.review { border-color: rgba(232,184,79,.45); }
  .tourney.review.sel { border-color: var(--gold); background: rgba(232,184,79,.08); }
  .tourney.review .tname { color: var(--gold); }

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
  .hand-head .net { font-weight: 700; font-size: 13px; width: 200px; text-align: right; white-space: nowrap; }
  .hand-head .ai-flag { width: 46px; text-align: right; font-size: 13px; letter-spacing: 1px; }
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
  .ai-spinner { display: inline-block; width: 11px; height: 11px; vertical-align: -1px;
    margin-left: 3px; border: 2px solid var(--green); border-top-color: transparent;
    border-radius: 50%; animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .ai-meta { color: var(--dim); font-size: 11px; margin-top: 6px; text-align: right; }

  #report-overlay { display: none; position: fixed; inset: 0; z-index: 50;
                    background: rgba(0,0,0,.62); align-items: center; justify-content: center; }
  #report-overlay.open { display: flex; }
  #report-panel { width: min(860px, 92vw); max-height: 86vh; overflow-y: auto;
                  background: var(--panel); border: 1px solid var(--border);
                  border-radius: 14px; padding: 20px 26px 24px; }
  #report-head { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
  #report-head h2 { font-size: 17px; }
  #report-body h2 { color: var(--gold); font-size: 15px; margin: 14px 0 6px; }
  #report-body ul { list-style: none; margin: 4px 0 10px 6px; }
  #report-body li { padding: 2px 0; }
  #report-body strong { color: #fff; }
  #report-body .ai-meta { margin-top: 14px; }

  .tourney.stats { border-color: rgba(77,163,255,.45); }
  .tourney.stats.sel { border-color: var(--accent); background: rgba(77,163,255,.1); }
  .tourney.stats .tname { color: var(--accent); }

  .stats-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(135px, 1fr));
                gap: 10px; margin-bottom: 20px; }
  .stat-card { background: var(--panel); border: 1px solid var(--border);
               border-radius: 10px; padding: 12px 14px; }
  .stat-card .v { font-size: 22px; font-weight: 700; letter-spacing: .3px; }
  .stat-card .l { color: var(--dim); font-size: 12px; margin-top: 2px; }
  .stat-card .sub { color: var(--dim); font-size: 11px; margin-top: 2px; }
  .stat-card .v.win { color: var(--green); } .stat-card .v.lose { color: var(--red); }

  .stat-section { margin-bottom: 22px; }
  .stat-section > h3 { font-size: 14px; color: var(--gold); margin-bottom: 8px; }
  .chart-box { background: var(--panel); border: 1px solid var(--border);
               border-radius: 10px; padding: 14px 16px; }
  table.stat-table { width: 100%; border-collapse: collapse; font-size: 13px;
                     background: var(--panel); border: 1px solid var(--border);
                     border-radius: 10px; overflow: hidden; }
  table.stat-table th, table.stat-table td { padding: 8px 12px; text-align: right;
                     border-bottom: 1px solid var(--border); }
  table.stat-table th { color: var(--dim); font-weight: 600; font-size: 12px;
                        background: var(--panel2); }
  table.stat-table td:first-child, table.stat-table th:first-child { text-align: left; }
  table.stat-table tr:last-child td { border-bottom: none; }
  table.stat-table tbody tr.clickable { cursor: pointer; }
  table.stat-table tbody tr.clickable:hover { background: var(--panel2); }
  .tnum.win { color: var(--green); } .tnum.lose { color: var(--red); }

  .grid-wrap { overflow-x: auto; padding-bottom: 4px; }
  table.hgrid { border-collapse: separate; border-spacing: 2px; }
  table.hgrid th { color: var(--dim); font-size: 11px; font-weight: 600;
                   width: 22px; height: 22px; text-align: center; }
  table.hgrid td { padding: 0; }
  .hgrid .hc { width: 52px; height: 44px; border-radius: 4px; background: var(--panel2);
               display: flex; flex-direction: column; align-items: center; justify-content: center;
               line-height: 1.15; border: 1px solid transparent; }
  .hgrid .hc.pair { border-color: rgba(232,184,79,.5); }
  .hgrid .hc .lab { font-weight: 700; font-size: 11px; }
  .hgrid .hc.mix .lab { text-shadow: 0 1px 2px rgba(0,0,0,.8); }
  .hgrid .hc .val { font-size: 10px; color: var(--text); opacity: .92; }
  .hgrid .hc.empty { opacity: .25; }
  .hgrid .hc.dim { opacity: .4; }

  .tourney.search { border-color: rgba(86,211,100,.4); }
  .tourney.search.sel { border-color: var(--green); background: rgba(86,211,100,.08); }
  .tourney.search .tname { color: var(--green); }

  #tsearch { flex: 1; min-width: 180px; max-width: 360px; }
  select.ts-sort { background: var(--panel2); border: 1px solid var(--border);
                   color: var(--text); border-radius: 6px; padding: 5px 8px; }
  .ts-counter { color: var(--dim); font-size: 12px; margin-bottom: 10px; }
  .ts-card { border: 1px solid var(--border); border-radius: 10px; background: var(--panel);
             padding: 11px 14px; margin-bottom: 8px; cursor: pointer; transition: border-color .1s; }
  .ts-card:hover { border-color: var(--accent); }
  .ts-card .ts-top { display: flex; align-items: center; gap: 8px; }
  .ts-card .ts-name { font-weight: 600; }
  .ts-card .ts-id { color: var(--dim); font-size: 12px; }
  .ts-card .ts-arrow { margin-left: auto; color: var(--dim); }
  .ts-card:hover .ts-arrow { color: var(--accent); }
  .ts-card .ts-meta { color: var(--dim); font-size: 12px; margin-top: 3px; }
  .ts-pager { display: flex; align-items: center; gap: 5px; justify-content: center;
              margin: 16px 0 8px; flex-wrap: wrap; }
  .ts-pager button { min-width: 34px; padding: 5px 9px; }
  .ts-pager button:disabled { opacity: .4; cursor: default; }
  .ts-pager button:disabled:hover { border-color: var(--border); color: var(--text); }
  .ts-ellip { color: var(--dim); padding: 0 2px; }

  .toast { position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
           background: var(--accent); color: #0c1117; font-weight: 600;
           padding: 9px 20px; border-radius: 8px; opacity: 0; transition: opacity .25s; }
  .toast.show { opacity: 1; }
</style>
</head>
<body>
<header>
  <h1>♠️ poker.gg</h1>
  <span class="spacer"></span>
  <button id="btnReport" onclick="openReport()" style="display:none">📊 종합 리포트</button>
  <button id="btnOpen">파일 열기</button>
  <input type="file" id="file" accept=".txt,.log" multiple style="display:none">
</header>

<div id="report-overlay" onclick="if(event.target===this) closeReport()">
  <div id="report-panel">
    <div id="report-head">
      <h2>📊 종합 리포트</h2>
      <span class="spacer"></span>
      <button onclick="closeReport()">닫기</button>
    </div>
    <div id="report-body"></div>
  </div>
</div>

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
let DATA = null, SEL = 0, HIDE_FOLDS = false;   // SEL: -1 복기, -2 통계, -3 검색, -4 드릴다운, -5 뱅크롤
let STACK_UNIT = 'chips';   // 스택 변화 차트 단위: 'chips'(절대 칩) | 'bb'
let DRILL = null;   // 그리드 칸 클릭 시 해당 조합 핸드 목록 ({id,name,hand_count,hands})
let BANKROLL = null, BANK_EDIT = null, BANK_SHOWFORM = false, BANK_FILTER = 'all', BANK_PREFILL = null, BANK_CHART = 'cum';
let BANK_PAGE = 0;
let BANK_TAB = 'results';   // 'results'(토너 성적) | 'cash'(입출금)
let BANK_CF_PAGE = 0;       // 입출금 내역 페이지
const BANK_PAGE_SIZE = 50;
let REPORT = null, ANALYZED_TOTAL = 0, REPORT_STREAMING = false;
let REVIEW_HANDS = null, REVIEW_COUNT = 0, STATS = null;
let REVIEW_PAGE = 0, REVIEW_UNANALYZED_ONLY = false, REVIEW_HIDE_ALLIN = false, REVIEW_BATCH = null;   // 복기: 페이징·필터·배치분석
const REVIEW_PAGE_SIZE = 50;
let SEARCH_Q = '', SEARCH_SORT = 'recent', SEARCH_PAGE = 0;
const SEARCH_PAGE_SIZE = 20;
let GRID_CACHE = {}, GRID_POS = 'all', GRID_STACK = 'all', GRID_METRIC = 'mix', STATS_TAB = 'summary';   // 통계 하위 탭
let LEAKS = null;   // 리크 리포트 캐시 (/api/leaks)

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
  // SEL >= 0 은 검색에서 연 토너먼트 핸드 뷰 → 🔍 토너먼트 항목을 활성 표시
  const inTourney = SEL >= 0;
  $('#sidebar').innerHTML = `
    <div class="tourney stats ${(SEL===-2||SEL===-4)?'sel':''}" onclick="selectStats()">
      <div class="tname">📈 통계</div>
      <div class="tmeta">포지션별 칩 EV · VPIP/PFR · WTSD</div>
    </div>
    <div class="tourney ${SEL===-5?'sel':''}" style="border-color:rgba(63,191,111,.4)" onclick="selectBankroll()">
      <div class="tname">💰 뱅크롤</div>
      <div class="tmeta">실제 손익($) · ROI · 토너 결과 입력</div>
    </div>
    <div class="tourney review ${SEL===-1?'sel':''}" onclick="selectReview()">
      <div class="tname">📌 복기 추천</div>
      <div class="tmeta">큰 손실 · 쇼다운/올인 패배 핸드 ${REVIEW_COUNT}개</div>
    </div>
    <div class="tourney search ${(SEL===-3||inTourney)?'sel':''}" onclick="selectSearch()">
      <div class="tname">🔍 토너먼트</div>
      <div class="tmeta">${DATA.tournaments.length}개 · 검색해서 열기</div>
    </div>`;
}

// 토너먼트 검색 뷰 — 사이드바 대신 본문에서 검색/페이징으로 토너 열기
function selectSearch() {
  SEL = -3; renderSidebar();
  $('#mainhead').innerHTML = `
    <h2 style="flex:0 0 auto">🔍 토너먼트</h2>
    <input type="text" id="tsearch" placeholder="이름 또는 #번호 검색..."
       value="${esc(SEARCH_Q)}" oninput="onSearchInput(this.value)">
    <label class="small">정렬
      <select class="ts-sort" onchange="onSearchSort(this.value)">
        <option value="recent">최신순</option>
        <option value="name">이름순</option>
        <option value="hands">핸드 많은 순</option>
      </select>
    </label>`;
  document.querySelector('.ts-sort').value = SEARCH_SORT;
  renderSearchResults();
  $('#main').scrollTop = 0;
  const inp = document.getElementById('tsearch');
  if (inp) { inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length); }
}

function onSearchInput(v) { SEARCH_Q = v; SEARCH_PAGE = 0; renderSearchResults(); }
function onSearchSort(v) { SEARCH_SORT = v; SEARCH_PAGE = 0; renderSearchResults(); }
function gotoSearchPage(p) { SEARCH_PAGE = p; renderSearchResults(); $('#main').scrollTop = 0; }

function filteredTournaments() {
  let list = DATA.tournaments.slice();
  const q = SEARCH_Q.trim().toLowerCase();
  if (q) list = list.filter(t =>
    (t.name || '').toLowerCase().includes(q) || String(t.id).toLowerCase().includes(q));
  if (SEARCH_SORT === 'name') list.sort((a, b) => (a.name || '').localeCompare(b.name || ''));
  else if (SEARCH_SORT === 'hands') list.sort((a, b) => b.hand_count - a.hand_count);
  else list.sort((a, b) => (b.start || '').localeCompare(a.start || ''));
  return list;
}

function pager(pages) {
  if (pages <= 1) return '';
  const cur = SEARCH_PAGE;
  const set = new Set([0, pages - 1, cur - 1, cur, cur + 1]);
  const nums = [...set].filter(p => p >= 0 && p < pages).sort((a, b) => a - b);
  let html = `<div class="ts-pager">
    <button ${cur === 0 ? 'disabled' : ''} onclick="gotoSearchPage(${cur - 1})">‹ 이전</button>`;
  let prev = -1;
  for (const p of nums) {
    if (prev >= 0 && p - prev > 1) html += `<span class="ts-ellip">…</span>`;
    html += `<button class="${p === cur ? 'primary' : ''}" onclick="gotoSearchPage(${p})">${p + 1}</button>`;
    prev = p;
  }
  html += `<button ${cur === pages - 1 ? 'disabled' : ''} onclick="gotoSearchPage(${cur + 1})">다음 ›</button></div>`;
  return html;
}

function renderSearchResults() {
  const all = filteredTournaments();
  const pages = Math.max(1, Math.ceil(all.length / SEARCH_PAGE_SIZE));
  if (SEARCH_PAGE >= pages) SEARCH_PAGE = pages - 1;
  if (SEARCH_PAGE < 0) SEARCH_PAGE = 0;
  const start = SEARCH_PAGE * SEARCH_PAGE_SIZE;
  const page = all.slice(start, start + SEARCH_PAGE_SIZE);
  const rows = page.map(t => {
    const idx = DATA.tournaments.indexOf(t);
    const end = (t.end || '').slice(11, 16);
    return `<div class="ts-card" onclick="selectTourney(${idx})">
      <div class="ts-top">
        <span class="ts-name">${esc(t.name || '')}</span>
        <span class="ts-id">#${esc(String(t.id))}</span>
        <span class="ts-arrow">→</span>
      </div>
      <div class="ts-meta">핸드 ${t.hand_count}${t.analyzed ? ' · 🤖' + t.analyzed : ''} · ${esc((t.start || '').slice(0, 16))}${end ? ' ~ ' + end : ''}</div>
    </div>`;
  }).join('');
  const counter = SEARCH_Q.trim()
    ? `${DATA.tournaments.length}개 중 <strong style="color:var(--text)">${all.length}</strong>개 검색됨`
    : `전체 ${all.length}개`;
  $('#hands').innerHTML = `
    <div class="ts-counter">${counter}</div>
    ${all.length ? rows : '<p style="color:var(--dim)">검색 결과가 없습니다.</p>'}
    ${pager(pages)}`;
}

// 통계 대시보드 뷰 — 전체 핸드 집계 (요약 / 핸드 그리드 탭)
async function selectStats() {
  SEL = -2; renderSidebar();
  $('#mainhead').innerHTML = '<h2>📈 통계</h2>';
  if (!STATS) {
    $('#hands').innerHTML = '<div class="ai-loading">집계 중</div>';
    const res = await fetch('/api/stats');
    STATS = await res.json();
  }
  if (SEL !== -2) return;
  renderStatsView(); $('#main').scrollTop = 0;
}

function setStatsTab(tab) { STATS_TAB = tab; renderStatsView(); $('#main').scrollTop = 0; }

async function renderStatsView() {
  $('#mainhead').innerHTML = `
    <h2 style="flex:0 0 auto">📈 통계</h2>
    <button class="${STATS_TAB === 'summary' ? 'primary' : ''}" onclick="setStatsTab('summary')">요약</button>
    <button class="${STATS_TAB === 'grid' ? 'primary' : ''}" onclick="setStatsTab('grid')">핸드 그리드</button>
    <button class="${STATS_TAB === 'leaks' ? 'primary' : ''}" onclick="setStatsTab('leaks')">🩹 리크</button>`;
  if (STATS_TAB === 'grid') {
    if (!GRID_CACHE[GRID_POS]) $('#hands').innerHTML = '<div class="ai-loading">핸드 그리드 집계 중</div>';
    await ensureGrid();
    if (SEL !== -2 || STATS_TAB !== 'grid') return;
    renderGrid();
  } else if (STATS_TAB === 'leaks') {
    if (!LEAKS) $('#hands').innerHTML = '<div class="ai-loading">리크 집계 중</div>';
    await ensureLeaks();
    if (SEL !== -2 || STATS_TAB !== 'leaks') return;
    renderLeaks();
  } else {
    renderStats();
  }
}

async function ensureLeaks() {
  if (!LEAKS) LEAKS = await fetch('/api/leaks').then(r => r.json());
  return LEAKS;
}

// 포지션+스택 조합별 그리드를 받아 캐시 (조합당 1회만 fetch)
function gridKey() { return GRID_POS + '|' + GRID_STACK; }
async function ensureGrid() {
  const k = gridKey();
  if (!GRID_CACHE[k]) {
    const params = [];
    if (GRID_POS !== 'all') params.push('pos=' + encodeURIComponent(GRID_POS));
    if (GRID_STACK !== 'all') params.push('stack=' + encodeURIComponent(GRID_STACK));
    const url = '/api/handgrid' + (params.length ? '?' + params.join('&') : '');
    GRID_CACHE[k] = await fetch(url).then(r => r.json());
  }
  return GRID_CACHE[k];
}

async function setGridFilter(kind, v) {
  if (kind === 'pos') { if (GRID_POS === v) return; GRID_POS = v; }
  else { if (GRID_STACK === v) return; GRID_STACK = v; }
  if (!GRID_CACHE[gridKey()]) $('#hands').innerHTML = '<div class="ai-loading">집계 중</div>';
  await ensureGrid();
  if (SEL !== -2 || STATS_TAB !== 'grid') return;
  renderGrid();
}
function setGridPos(p) { return setGridFilter('pos', p); }
function setGridStack(s) { return setGridFilter('stack', s); }

// --- 스타팅 핸드 13×13 매트릭스 ---
const GRID_RANKS = 'AKQJT98765432'.split('');

// 행 i, 열 j → 조합 라벨 (대각선=페어, ↗위=수딧, ↙아래=오프수딧)
function comboLabel(i, j) {
  const hi = GRID_RANKS[Math.min(i, j)], lo = GRID_RANKS[Math.max(i, j)];
  if (i === j) return hi + lo;
  return hi + lo + (i < j ? 's' : 'o');
}

function setGridMetric(m) { GRID_METRIC = m; renderGrid(); }

function gridTd(cells, i, j, maxAbs) {
  const combo = comboLabel(i, j);
  const d = cells[combo];
  const pairCls = i === j ? ' pair' : '';
  if (!d || !d.n) return `<td><div class="hc empty${pairCls}"><div class="lab">${combo}</div></div></td>`;
  const n = d.n;
  const vpip = Math.round(100 * d.vpip / n), pfr = Math.round(100 * d.pfr / n);
  const opp = d.rfi_opp || 0;
  const rfiPct = opp ? Math.round(100 * d.rfi / opp) : null;   // RFI는 기회(폴드 투 히어로) 대비
  // 액션 구성 모드 — 셀을 오픈/3벳/콜/올인/폴드 스택바로 채움
  if (GRID_METRIC === 'mix') {
    // 레이즈 계열을 옅은→진한 블루 램프로 연속 배치 (오픈→3벳→올인), 그 뒤 콜(틸)·폴드(회색)
    const po = 100 * (d.open || 0) / n, ptb = 100 * (d.tb || 0) / n;
    const pai = 100 * (d.allin || 0) / n, pcl = 100 * (d.call || 0) / n;
    const s1 = po, s2 = s1 + ptb, s3 = s2 + pai, s4 = s3 + pcl;
    const a = 0.7;   // 반투명 — 셀 배경(panel2) 위에 은은하게 얹힘, 폴드는 투명
    const bg = `linear-gradient(90deg,rgba(124,196,255,${a}) 0 ${s1}%,rgba(74,143,224,${a}) ${s1}% ${s2}%,` +
               `rgba(44,91,208,${a}) ${s2}% ${s3}%,rgba(45,212,167,${a}) ${s3}% ${s4}%,transparent ${s4}% 100%)`;
    const t = `${combo} · ${n}핸드 · 오픈 ${Math.round(po)}% · 3벳 ${Math.round(ptb)}% · ` +
              `올인 ${Math.round(pai)}% · 콜 ${Math.round(pcl)}% · 폴드 ${Math.round(100 - s4)}%`;
    return `<td><div class="hc mix${pairCls}" style="background-image:${bg};cursor:pointer" title="${t} — 클릭하면 핸드 보기" onclick="drillCombo('${combo}')"><div class="lab">${combo}</div></div></td>`;
  }
  let val, bg, dim = '';
  if (GRID_METRIC === 'bb') {
    val = (d.bb >= 0 ? '+' : '') + Math.round(d.bb);
    const t = Math.min(1, Math.abs(d.bb) / maxAbs);
    const rgb = d.bb >= 0 ? '63,191,111' : '224,85,106';
    bg = `rgba(${rgb},${(0.1 + 0.6 * t).toFixed(2)})`;
    if (n < 20) dim = ' dim';
  } else if (GRID_METRIC === 'rfi') {
    if (rfiPct === null) { val = '·'; bg = 'var(--panel2)'; dim = ' dim'; }   // 오픈 기회 없던 조합
    else { val = rfiPct; bg = `rgba(77,163,255,${(rfiPct / 100 * 0.6).toFixed(2)})`; if (opp < 10) dim = ' dim'; }
  } else {
    const pct = GRID_METRIC === 'vpip' ? vpip : pfr;
    val = pct;
    bg = `rgba(77,163,255,${(pct / 100 * 0.6).toFixed(2)})`;
  }
  const rfiTxt = opp ? `${Math.round(100 * d.rfi / opp)}% (${opp}회 기회)` : '기회없음';
  const title = `${combo} · ${n}핸드 · VPIP ${vpip}% · PFR ${pfr}% · RFI ${rfiTxt} · 칩EV ${d.bb >= 0 ? '+' : ''}${d.bb}bb`;
  return `<td><div class="hc${dim}${pairCls}" style="background:${bg};cursor:pointer" title="${title} — 클릭하면 핸드 보기" onclick="drillCombo('${combo}')">
    <div class="lab">${combo}</div><div class="val">${val}</div></div></td>`;
}

function renderGrid() {
  const cells = (GRID_CACHE[gridKey()] && GRID_CACHE[gridKey()].cells) || {};
  let maxAbs = 1, totalN = 0, totalOpp = 0, totalActs = 0;
  for (const k in cells) {
    maxAbs = Math.max(maxAbs, Math.abs(cells[k].bb));
    totalN += cells[k].n; totalOpp += (cells[k].rfi_opp || 0);
    totalActs += (cells[k].open || 0) + (cells[k].tb || 0) + (cells[k].call || 0) + (cells[k].allin || 0);
  }
  let rows = '<tr><th></th>' + GRID_RANKS.map(r => `<th>${r}</th>`).join('') + '</tr>';
  for (let i = 0; i < 13; i++) {
    let tds = `<th>${GRID_RANKS[i]}</th>`;
    for (let j = 0; j < 13; j++) tds += gridTd(cells, i, j, maxAbs);
    rows += `<tr>${tds}</tr>`;
  }
  const mBtn = (k, l) => `<button class="${GRID_METRIC === k ? 'primary' : ''}" onclick="setGridMetric('${k}')">${l}</button>`;
  const pBtn = (k, l) => `<button class="${GRID_POS === k ? 'primary' : ''}" onclick="setGridPos('${k}')">${l}</button>`;
  const sBtn = (k, l) => `<button class="${GRID_STACK === k ? 'primary' : ''}" onclick="setGridStack('${k}')">${l}</button>`;
  const positions = ((STATS && STATS.positions) || []).map(p => p.pos).filter(p => p !== '?');
  const stacks = [['all', '전체'], ['pf', '<15'], ['short', '15-25'], ['mid', '25-40'], ['deep', '40+']];
  const stackLabel = {all: '전체 스택', pf: '<15bb', short: '15-25bb', mid: '25-40bb', deep: '40bb+'}[GRID_STACK];
  const unit = {bb: '칩 EV(bb)', rfi: 'RFI%(오픈)', mix: '액션 비율'}[GRID_METRIC];
  const posLabel = GRID_POS === 'all' ? '전체 포지션' : GRID_POS;
  // RFI/구성 모드인데 데이터가 0 → 기존 DB에 해당 필드 없음
  const needRebuild = (GRID_METRIC === 'rfi' && totalOpp === 0) || (GRID_METRIC === 'mix' && totalActs === 0 && totalN > 0);
  const rebuildHint = needRebuild
    ? `<div class="ai-error" style="color:var(--gold)">이 지표는 <code>python3 gui.py --rebuild</code>로 1회 재변환해야 표시됩니다 (기존 DB엔 해당 필드가 없음).</div>`
    : '';
  const chip = (c, l) => `<span style="display:inline-flex;align-items:center;gap:4px"><span style="width:11px;height:11px;border-radius:2px;background:${c};display:inline-block"></span>${l}</span>`;
  const legend = GRID_METRIC === 'mix'
    ? `<div style="display:flex;gap:14px;margin-bottom:10px;flex-wrap:wrap;color:var(--dim);font-size:12px">
         ${chip('rgba(124,196,255,.7)', '오픈')} ${chip('rgba(74,143,224,.7)', '3벳')} ${chip('rgba(44,91,208,.7)', '올인')} ${chip('rgba(45,212,167,.7)', '콜')} ${chip('var(--panel2)', '폴드')}
       </div>` : '';
  const note = GRID_METRIC === 'mix'
    ? ' 각 칸을 프리플랍 첫 액션 비율로 채움 (바 길이=VPIP). 칸 호버로 정확한 %.'
    : (GRID_METRIC === 'rfi'
      ? ' RFI는 폴드로 히어로까지 온 경우(오픈 기회) 대비 첫 레이즈 비율 — 솔버 오픈 차트와 같은 정의. 기회 10회 미만은 흐리게, 기회 없던 칸은 · 표시.'
      : ' 포지션별로 보면 표본이 작아지니 칩 EV는 참고만 (20핸드 미만 흐리게).');
  $('#hands').innerHTML = `
    <div style="display:flex;align-items:center;gap:6px;margin-bottom:8px;flex-wrap:wrap">
      <span style="color:var(--dim);font-size:13px">포지션:</span>
      ${pBtn('all', '전체')} ${positions.map(p => pBtn(p, p)).join(' ')}
    </div>
    <div style="display:flex;align-items:center;gap:6px;margin-bottom:8px;flex-wrap:wrap">
      <span style="color:var(--dim);font-size:13px">스택(bb):</span>
      ${stacks.map(([k, l]) => sBtn(k, l)).join(' ')}
    </div>
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px;flex-wrap:wrap">
      <span style="color:var(--dim);font-size:13px">표시 기준:</span>
      ${mBtn('mix', '액션')} ${mBtn('rfi', 'RFI')} ${mBtn('bb', '칩 EV')}
      <span style="color:var(--dim);font-size:12px;margin-left:auto">${esc(posLabel)} · ${esc(stackLabel)} · ${totalN.toLocaleString()}핸드 · ${unit}</span>
    </div>
    ${legend}${rebuildHint}
    <div class="grid-wrap"><table class="hgrid">${rows}</table></div>
    <p style="color:var(--dim);font-size:12px;margin-top:10px">
      대각선=페어 · ↗ 수딧 · ↙ 오프수딧. 칸에 마우스를 올리면 핸드 수·VPIP·PFR·RFI·칩 EV 전체가, <strong style="color:var(--text)">클릭하면 해당 조합 핸드 목록</strong>이 열립니다.${note}</p>`;
}

function statCard(val, label, sub, cls) {
  return `<div class="stat-card">
    <div class="v ${cls || ''}">${val}</div>
    <div class="l">${label}</div>
    ${sub ? `<div class="sub">${sub}</div>` : ''}
  </div>`;
}

function bbCell(v) {
  const cls = v >= 0 ? 'win' : 'lose';
  return `<span class="tnum ${cls}">${v >= 0 ? '+' : ''}${v.toLocaleString()}</span>`;
}

function renderStats() {
  const s = STATS;
  if (!s || !s.total) {
    $('#hands').innerHTML = '<p style="color:var(--dim)">집계할 핸드가 없습니다.</p>';
    return;
  }
  const pfr = s.pfr_pct === null
    ? statCard('—', 'PFR', '<code>--rebuild</code> 후 표시')
    : statCard(s.pfr_pct + '%', 'PFR',
        s.pfr_known < s.total ? `${s.pfr_known.toLocaleString()}핸드 기준` : '프리플랍 레이즈');

  const cards = [
    statCard(s.total.toLocaleString(), '핸드', `${s.tournaments}개 토너먼트`),
    statCard(s.vpip_pct + '%', 'VPIP', '자발적 팟 참여'),
    pfr,
    statCard(s.wtsd_pct + '%', 'WTSD', 'VPIP 대비 쇼다운'),
    statCard(s.wsd_pct + '%', 'W$SD', `쇼다운 ${s.showdown}회 중 승`),
  ].join('');

  const posRows = s.positions.map(p => {
    const vpip = p.hands ? Math.round(100 * p.vpip / p.hands) : 0;
    return `<tr>
      <td>${esc(p.pos)}</td><td>${p.hands.toLocaleString()}</td>
      <td>${vpip}%</td><td>${bbCell(p.net_bb)}</td></tr>`;
  }).join('');

  $('#hands').innerHTML = `
    <div class="stats-grid">${cards}</div>
    <div class="stat-section">
      <h3>포지션별 <span style="color:var(--dim);font-size:12px;font-weight:400">(칩 EV — 플레이 품질 지표, 상금 아님)</span></h3>
      <table class="stat-table">
        <thead><tr><th>포지션</th><th>핸드</th><th>VPIP</th><th>칩 EV(bb)</th></tr></thead>
        <tbody>${posRows}</tbody>
      </table>
    </div>`;
}

// --- 🩹 리크 대시보드 (AI 등급 집계) ---
const LEAK_GRADES = ['좋음', '무난', '의문', '실수'];
const LEAK_COLOR = {'좋음': 'var(--green)', '무난': 'var(--accent)', '의문': 'var(--gold)', '실수': 'var(--red)'};

// 전체 평가 분포 — 가로 스택 막대 + 범례
function leakOverallBar(o) {
  const tot = LEAK_GRADES.reduce((a, g) => a + (o[g] || 0), 0);
  if (!tot) return '<p style="color:var(--dim)">총평 등급이 집계된 핸드가 없습니다.</p>';
  const bars = LEAK_GRADES.map(g => {
    const v = o[g] || 0; if (!v) return '';
    const pct = 100 * v / tot;
    return `<div title="${g} ${v}핸드" style="width:${pct}%;background:${LEAK_COLOR[g]};display:flex;align-items:center;justify-content:center;font-size:11px;color:#0c1117;font-weight:600">${pct >= 9 ? Math.round(pct) + '%' : ''}</div>`;
  }).join('');
  const legend = LEAK_GRADES.map(g =>
    `<span style="font-size:12px;color:var(--dim)"><span style="display:inline-block;width:9px;height:9px;background:${LEAK_COLOR[g]};border-radius:2px;margin-right:4px;vertical-align:middle"></span>${VERDICT_EMOJI[g]} ${g} ${o[g] || 0}</span>`
  ).join('  ');
  return `<div style="display:flex;height:22px;border-radius:5px;overflow:hidden;margin:8px 0 6px">${bars}</div>
    <div style="display:flex;gap:16px;flex-wrap:wrap">${legend}</div>`;
}

// 리크율 막대 한 줄 (의문+실수 / 평가횟수). dimUnder 미만 표본은 흐리게.
function leakRateRow(label, leak, evald, isMax, dimUnder) {
  const rate = evald ? Math.round(100 * leak / evald) : 0;
  const dim = evald < (dimUnder || 0);
  const col = rate >= 35 ? 'var(--red)' : rate >= 20 ? 'var(--gold)' : 'var(--accent)';
  return `<div style="display:flex;align-items:center;gap:10px;margin:6px 0;${dim ? 'opacity:.4' : ''}">
    <div style="width:62px;color:var(--dim);font-size:13px;text-align:right">${esc(label)}</div>
    <div style="flex:1;height:14px;background:var(--panel2);border-radius:4px;overflow:hidden">
      <div style="width:${Math.min(100, rate)}%;height:100%;background:${col}"></div>
    </div>
    <div style="width:120px;font-size:12px">${evald ? rate + '%' : '—'} <span style="color:var(--dim)">(${leak}/${evald})</span>${isMax && rate > 0 ? ' 🔴' : ''}</div>
  </div>`;
}

function renderLeaks() {
  const L = LEAKS;
  if (!L) return;
  if (!L.analyzed) {
    $('#hands').innerHTML = `<p style="color:var(--dim)">AI 분석된 핸드가 없습니다. 📌 복기 탭에서 핸드를 분석하면 여기에 리크가 집계됩니다.</p>`;
    return;
  }
  const pct = Math.round(100 * L.analyzed / L.total);

  // 스트리트별 리크율 (의문+실수 / 4등급 합)
  const streetData = L.streets.map(s => {
    const evald = LEAK_GRADES.reduce((a, g) => a + (s[g] || 0), 0);
    return {label: s.street, leak: (s['의문'] || 0) + (s['실수'] || 0), evald};
  });
  const streetMax = Math.max(0, ...streetData.filter(d => d.evald >= 10).map(d => d.evald ? d.leak / d.evald : 0));
  const streetRows = streetData.map(d =>
    leakRateRow(d.label, d.leak, d.evald, d.evald >= 10 && d.evald && d.leak / d.evald === streetMax && streetMax > 0, 5)
  ).join('');

  // 포지션별 리크율 (총평 등급 기준)
  const posMax = Math.max(0, ...L.positions.filter(p => p.n >= 10).map(p => p.n ? p.leak / p.n : 0));
  const posRows = L.positions.map(p =>
    leakRateRow(p.pos, p.leak, p.n, p.n >= 10 && p.n && p.leak / p.n === posMax && posMax > 0, 10)
  ).join('');

  // 가장 큰 리크 핸드 (실수 우선 · 칩손실 큰 순)
  const handRows = L.leak_hands.map(h => {
    const nb = h.net_bb;
    const netH = nb == null ? '' :
      `<span style="color:${nb >= 0 ? 'var(--green)' : 'var(--red)'};font-size:12px">${nb >= 0 ? '+' : ''}${nb.toFixed(1)}bb</span>`;
    return `<div onclick="openHandFromLeak('${h.tournament_id}','${h.hand_id}')"
        style="display:flex;align-items:center;gap:9px;padding:7px 8px;border-bottom:1px solid var(--border);cursor:pointer"
        onmouseover="this.style.background='var(--panel2)'" onmouseout="this.style.background=''">
      <span style="width:22px;text-align:center">${VERDICT_EMOJI[h.grade] || ''}</span>
      <span class="pos pos-badge" style="min-width:42px;text-align:center">${esc(h.hero_pos)}</span>
      <span style="width:46px;color:var(--dim);font-size:12px">${esc(h.street)}</span>
      <span style="width:52px">${cardsHtml(h.hero_cards)}</span>
      <span style="width:60px;text-align:right">${netH}</span>
      <span style="flex:1;color:var(--dim);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(h.snippet || '')}</span>
      <span style="color:var(--dim)">›</span>
    </div>`;
  }).join('') || '<p style="color:var(--dim);padding:8px">의문·실수로 분류된 핸드가 없습니다. 👍</p>';

  $('#hands').innerHTML = `
    <div style="color:var(--dim);font-size:13px;margin-bottom:14px">
      분석된 <strong style="color:var(--text)">${L.analyzed.toLocaleString()}</strong> / ${L.total.toLocaleString()}핸드 기준 (${pct}%)
      · <span style="font-size:12px">표본 적은 항목은 흐리게</span>
    </div>
    <div class="stat-section">
      <h3>전체 평가 분포</h3>
      ${leakOverallBar(L.overall)}
    </div>
    <div class="stat-section">
      <h3>스트리트별 리크율 <span style="color:var(--dim);font-size:12px;font-weight:400">(의문+실수 비율 · 어디서 새는지)</span></h3>
      ${streetRows}
    </div>
    <div class="stat-section">
      <h3>포지션별 리크율 <span style="color:var(--dim);font-size:12px;font-weight:400">(총평 등급 기준)</span></h3>
      ${posRows}
    </div>
    <div class="stat-section">
      <h3>❌ 가장 큰 리크 핸드 <span style="color:var(--dim);font-size:12px;font-weight:400">(실수 우선 · 칩손실 큰 순 · 클릭→핸드)</span></h3>
      <div style="border:1px solid var(--border);border-radius:8px;overflow:hidden">${handRows}</div>
    </div>`;
}

// 리크 핸드 클릭 → 해당 토너먼트 열고 그 핸드로 스크롤·펼치기·하이라이트
async function openHandFromLeak(tid, handId) {
  const i = DATA.tournaments.findIndex(t => t.id === tid);
  if (i < 0) { toast('연결된 핸드를 찾을 수 없습니다'); return; }
  await selectTourney(i);
  requestAnimationFrame(() => {
    const box = document.getElementById('ai-' + handId);
    const card = box && box.closest('.hand');
    if (!card) { toast('핸드를 찾지 못했습니다'); return; }
    card.classList.add('open');
    card.scrollIntoView({behavior: 'smooth', block: 'center'});
    card.style.transition = 'background .4s';
    card.style.background = 'rgba(232,184,79,.16)';
    setTimeout(() => { card.style.background = ''; }, 1500);
  });
}

// 복기 추천 뷰 — 전체 토너에서 추천 핸드만 모아서 표시
async function selectReview() {
  SEL = -1; REVIEW_PAGE = 0; renderSidebar();
  if (!REVIEW_HANDS) {
    $('#mainhead').innerHTML = '<h2>📌 복기 추천</h2>';
    $('#hands').innerHTML = '<div class="ai-loading">핸드 불러오는 중</div>';
    const res = await fetch('/api/review');
    const data = await res.json();
    if (SEL !== -1) return;
    REVIEW_HANDS = data.hands;
    for (const h of REVIEW_HANDS)
      if (h.analysis && !AI_CACHE[h.hand_id])
        AI_CACHE[h.hand_id] = {status: 'done', text: h.analysis, backend: '저장됨'};
  }
  renderMain(); $('#main').scrollTop = 0;
}

// 현재 선택된 뷰 (토너먼트 / 복기 추천 / 그리드 드릴다운)
function currentTourney() {
  if (SEL === -1) {
    const hands = REVIEW_HANDS || [];
    return {id: 'review', name: '📌 복기 추천', hand_count: hands.length, hands};
  }
  if (SEL === -4) return DRILL || {id: 'drill', name: '🃏', hand_count: 0, hands: []};
  return DATA.tournaments[SEL];
}

// 현재 표시 대상 핸드 (필터 적용) — 지연 로딩 전이면 빈 배열
function visibleHands() {
  const t = currentTourney();
  const hands = (t && t.hands) || [];
  return HIDE_FOLDS ? hands.filter(h => !h.no_action_fold) : hands;
}

function toggleFolds() { HIDE_FOLDS = !HIDE_FOLDS; renderMain(); }

function setStackUnit(u) { if (STACK_UNIT === u) return; STACK_UNIT = u; renderMain(); }

// 리바이 감지: hand_id Set 반환 (리바이로 돌아온 직후 첫 핸드들)
function detectRebuys(allHands) {
  const pairs = allHands
    .filter(h => h.stack_bb != null && h.blinds)
    .map(h => { const b = parseFloat((h.blinds || '').split('/')[1]) || 0; return b ? {hand: h, chips: Math.round(h.stack_bb * b)} : null; })
    .filter(v => v != null);
  const ids = new Set();
  for (let i = 0; i < pairs.length - 1; i++) {
    const expected = Math.max(0, pairs[i].chips + (pairs[i].hand.net || 0));
    const bbVal = parseFloat((pairs[i].hand.blinds || '').split('/')[1]) || 100;
    if (pairs[i + 1].chips > expected + bbVal * 2) ids.add(pairs[i + 1].hand.hand_id);   // 리바이로 돌아온 첫 판에 태그
  }
  return ids;
}

function _fmtChips(v) { return v >= 1000 ? (v / 1000).toFixed(1).replace(/\.0$/, '') + 'k' : String(Math.round(v)); }

function stackChartHover(e, id) {
  const d = window[id]; if (!d) return;
  const svg = e.currentTarget;
  const rect = svg.getBoundingClientRect();
  const vx = (e.clientX - rect.left) / rect.width * d.W;
  const idx = Math.max(0, Math.min(d.pts.length - 1, Math.round(vx / d.W * (d.pts.length - 1))));
  const tip = document.getElementById(id + '_tip'); if (!tip) return;
  const hand = d.hands[idx];
  const chips = d.pts[idx];
  tip.style.display = 'block';
  const tx = e.clientX - rect.left, ty = e.clientY - rect.top;
  tip.style.left = (tx + 12) + 'px';
  tip.style.top = Math.max(0, ty - 28) + 'px';
  const unit = d.unit === 'bb' ? ' bb' : ' chips';
  const txt = d.unit === 'bb' ? (Math.round(chips * 10) / 10) : _fmtChips(chips);
  tip.textContent = (hand ? '#' + hand.hand_id + '  ' : '') + txt + unit;
}
function stackChartHide(id) { const t = document.getElementById(id + '_tip'); if (t) t.style.display = 'none'; }

// 토너먼트 스택 변화 차트 (절대 칩량 / BB 토글)
function tourneyStackChart(allHands) {
  const pairs = allHands
    .filter(h => h.stack_bb != null && h.blinds)
    .map(h => {
      const b = parseFloat((h.blinds || '').split('/')[1]) || 0;
      if (!b) return null;
      const chips = Math.round(h.stack_bb * b);
      // 단위에 맞춰 표시값(val)·핸드 손익(net) 동시 보유
      return STACK_UNIT === 'bb'
        ? {hand: h, val: h.stack_bb, net: (h.net || 0) / b}
        : {hand: h, val: chips, net: h.net || 0};
    })
    .filter(v => v != null);
  if (pairs.length < 2) return '';
  const valid = pairs.map(p => p.hand);
  const pts = pairs.map(p => p.val);
  // 세그먼트 분할: 버스트+리바이 시점에 선을 끊고 리바이 시작점에 점 마커 표시
  const drawPts = [];
  const drawHands = [];  // drawPts 인덱스 → hand (버스트/최종점은 null)
  const segments = [[]];   // 세그먼트별 drawPts 인덱스 목록
  const rebuyDots = [];    // 리바이 시작점 drawPts 인덱스
  let rebuyCount = 0;
  const gap = STACK_UNIT === 'bb' ? 2 : null;   // 리바이 판정 임계 (bb: 2bb, chips: 2*BB)
  for (let i = 0; i < valid.length; i++) {
    const idx = drawPts.length;
    drawPts.push(pts[i]); drawHands.push(valid[i]);
    segments[segments.length - 1].push(idx);
    const end = Math.max(0, pts[i] + (pairs[i].net || 0));
    const thresh = gap != null ? gap : (parseFloat((valid[i].blinds || '').split('/')[1]) || 100) * 2;
    if (i < valid.length - 1 && pts[i + 1] > end + thresh) {
      const bustIdx = drawPts.length;
      drawPts.push(end); drawHands.push(null);   // 버스트 → 0
      segments[segments.length - 1].push(bustIdx);
      segments.push([]);                         // 새 세그먼트 (선 끊김)
      rebuyDots.push(drawPts.length);            // 다음에 push될 인덱스 = 리바이 시작점
      rebuyCount++;
    }
  }
  const finalIdx = drawPts.length;
  drawPts.push(Math.max(0, pts[pts.length - 1] + (pairs[pairs.length - 1].net || 0)));
  drawHands.push(null);
  segments[segments.length - 1].push(finalIdx);
  const W = 900, H = 175;
  const mn = Math.min(...drawPts), mx = Math.max(...drawPts), range = mx - mn || 1;
  const X = i => (i / (drawPts.length - 1) * W).toFixed(1);
  const Y = v => (H - (v - mn) / range * H).toFixed(1);
  const start = drawPts[0], last = drawPts[drawPts.length - 1];
  const color = last >= start ? 'var(--green)' : 'var(--red)';
  const polylines = segments
    .filter(seg => seg.length >= 2)
    .map(seg => `<polyline points="${seg.map(i => `${X(i)},${Y(drawPts[i])}`).join(' ')}"
      fill="none" stroke="${color}" stroke-width="2" vector-effect="non-scaling-stroke"/>`)
    .join('');
  const dots = rebuyDots.map(i => {
    const cx = parseFloat(X(i)), cy = parseFloat(Y(drawPts[i])), s = 2.5;
    return `<polygon points="${cx},${cy-s} ${cx+s},${cy} ${cx},${cy+s} ${cx-s},${cy}"
      fill="${color}" vector-effect="non-scaling-stroke"/>`;
  }).join('');
  const rebuyLabel = rebuyCount
    ? ` · <span style="color:var(--gold)">리바이 ${rebuyCount}회</span>` : '';
  const cid = 'sc_' + Date.now();
  window[cid] = { pts: drawPts, hands: drawHands, W, unit: STACK_UNIT };
  const uBtn = (k, l) => `<button onclick="setStackUnit('${k}')" style="font-size:11px;padding:2px 9px;border-radius:6px;border:1px solid var(--border);cursor:pointer;${STACK_UNIT === k ? 'background:var(--accent);color:#0c1117;font-weight:600' : 'background:transparent;color:var(--dim)'}">${l}</button>`;
  return `<div style="background:var(--panel);border:1px solid var(--border);border-radius:9px;padding:10px 12px;margin-bottom:14px;max-width:900px">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
      <div style="color:var(--dim);font-size:12px">스택 변화 · ${valid.length}핸드${rebuyLabel}</div>
      <div style="display:flex;gap:4px">${uBtn('chips', '칩')}${uBtn('bb', 'BB')}</div>
    </div>
    <div style="position:relative">
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="width:100%;height:175px;display:block;cursor:crosshair"
        onmousemove="stackChartHover(event,'${cid}')" onmouseleave="stackChartHide('${cid}')">
        <line x1="0" y1="${Y(0)}" x2="${W}" y2="${Y(0)}" stroke="var(--border)" stroke-width="1"/>
        ${polylines}
        ${dots}
      </svg>
      <div id="${cid}_tip" style="display:none;position:absolute;pointer-events:none;background:rgba(20,20,30,0.92);color:var(--text);font-size:11px;padding:3px 8px;border-radius:4px;white-space:nowrap;border:1px solid var(--border)"></div>
    </div>
  </div>`;
}

// 복기 핸드 분석 완료 여부 (저장된 analysis 또는 이번 세션 AI_CACHE done)
function reviewAnalyzed(h) {
  const c = AI_CACHE[h.hand_id];
  return !!(h.analysis || (c && c.status === 'done'));
}

function renderMain() {
  const t = currentTourney();
  let hands = visibleHands();
  let countLabel = HIDE_FOLDS
    ? `${hands.length}핸드 표시 (프리폴드 ${t.hand_count - hands.length}개 숨김)`
    : `${t.hand_count}핸드`;

  // 복기 탭 전용: 미분석/프리올인 필터 + 페이징(50개씩) + 배치 분석. 필터는 별도 필터 바로 분리.
  let pager = '', banner = '', filterBar = '', headBtns;
  if (SEL === -1) {
    const f = reviewFiltered();          // 프리올인·미분석 필터 적용 (페이징 전)
    hands = f.list;
    const total = hands.length;
    const pages = Math.max(1, Math.ceil(total / REVIEW_PAGE_SIZE));
    if (REVIEW_PAGE >= pages) REVIEW_PAGE = pages - 1;
    if (REVIEW_PAGE < 0) REVIEW_PAGE = 0;
    hands = hands.slice(REVIEW_PAGE * REVIEW_PAGE_SIZE, (REVIEW_PAGE + 1) * REVIEW_PAGE_SIZE);
    const pageUnan = hands.filter(h => !reviewAnalyzed(h)).length;
    const running = !!(REVIEW_BATCH && REVIEW_BATCH.running);
    // 헤더: 기능 버튼만 (배치 분석 + 펼치기/접기). 복사·다운로드는 복기에서 제거.
    headBtns = `
      <button class="primary" onclick="reviewAnalyzePage()" ${running || pageUnan === 0 ? 'disabled' : ''}>🤖 이 페이지 분석 (미분석 ${pageUnan})</button>
      <button onclick="toggleAll(true)">모두 펼치기</button>
      <button onclick="toggleAll(false)">모두 접기</button>`;
    // 필터 바: 필터만 따로 묶음
    filterBar = `<div style="display:flex;align-items:center;gap:8px;padding:8px 12px;margin-bottom:12px;background:var(--panel);border:1px solid var(--border);border-radius:9px">
      <span style="color:var(--dim);font-size:12px;margin-right:2px">필터</span>
      <button onclick="reviewToggleUnanalyzed()" class="${REVIEW_UNANALYZED_ONLY ? 'primary' : ''}">${REVIEW_UNANALYZED_ONLY ? '✓ ' : ''}미분석만</button>
      <button onclick="reviewToggleAllin()" class="${REVIEW_HIDE_ALLIN ? 'primary' : ''}" title="히어로가 프리플랍에 올인(푸시)한 핸드 숨김">${REVIEW_HIDE_ALLIN ? '✓ ' : ''}프리올인 숨기기</button>
    </div>`;
    pager = reviewPager(pages, total, f.totalUnan);
    if (running) banner = reviewBatchBanner();
    countLabel = `${t.hand_count}핸드`;
  } else {
    headBtns = `
      <button onclick="toggleFolds()" class="${HIDE_FOLDS ? 'primary' : ''}">${HIDE_FOLDS ? '✓ ' : ''}프리폴드 숨기기</button>
      <button onclick="toggleAll(true)">모두 펼치기</button>
      <button onclick="toggleAll(false)">모두 접기</button>
      <button onclick="copyMd()">📋 마크다운 복사</button>
      <button class="primary" onclick="downloadMd()">⬇ .md 다운로드</button>`;
  }

  const backBtn = SEL === -4 ? `<button onclick="backToGrid()">← 그리드로</button>`
    : SEL >= 0 ? `<button onclick="selectSearch()">← 검색으로</button>` : '';
  $('#mainhead').innerHTML = `
    ${backBtn}
    <h2>${esc(t.name)} <span style="color:var(--dim);font-size:13px">#${t.id} · ${countLabel}</span></h2>
    ${headBtns}`;
  const rebuyIds = detectRebuys(t.hands || []);
  $('#hands').innerHTML = banner + filterBar + (SEL !== -1 ? tourneyStackChart(t.hands || []) : '') + pager + hands.map((h, i) => {
    const tags = [];
    if (rebuyIds.has(h.hand_id)) tags.push('<span style="color:var(--gold)">리바이</span>');
    if (!h.vpip) tags.push('fold');
    if (h.showdown) tags.push('showdown');
    if (SEL === -1 && h.tournament_name) tags.unshift(esc(h.tournament_name));  // 복기 뷰: 출처 토너
    if (h.review && h.review.length) tags.push('📌 ' + h.review.join('·'));
    return `
    <div class="hand" id="hand${i}">
      <div class="hand-head" onclick="document.getElementById('hand${i}').classList.toggle('open')">
        <span class="hid">#${h.hand_id.slice(-6)} ${esc(h.datetime.slice(11,19))}</span>
        <span class="pos pos-badge">${h.hero_pos || '?'}</span>
        <span class="cards">${cardsHtml(h.hero_cards)}</span>
        <span class="tags">${h.blinds} · ${h.players}p${tags.length ? ' · ' + tags.join(' · ') : ''}</span>
        <span class="ai-flag" id="flag-${h.hand_id}">${aiFlagHtml(h.hand_id)}</span>
        ${netHtml(h.net, h.net_bb)}
      </div>
      <div class="hand-body">
        ${mdToHtml(stripHeader(h.markdown))}
        <div class="ai-box" id="ai-${h.hand_id}">${aiBoxHtml(h.hand_id)}</div>
      </div>
    </div>`;
  }).join('') + pager;
}

// 복기 페이저 + 핸드/미분석 카운트
function reviewPager(pages, total, totalUnan) {
  const info = `${total}핸드${REVIEW_UNANALYZED_ONLY ? '(미분석)' : ` · 미분석 ${totalUnan}`}${pages > 1 ? ` · ${REVIEW_PAGE + 1}/${pages}p` : ''}`;
  if (pages <= 1) return `<div style="color:var(--dim);font-size:12px;margin:8px 0;text-align:center">${info}</div>`;
  return `<div style="display:flex;gap:6px;justify-content:center;align-items:center;margin:8px 0">
    <button ${REVIEW_PAGE <= 0 ? 'disabled' : ''} onclick="reviewGotoPage(${REVIEW_PAGE - 1})">‹</button>
    <span style="color:var(--dim);font-size:12px">${info}</span>
    <button ${REVIEW_PAGE >= pages - 1 ? 'disabled' : ''} onclick="reviewGotoPage(${REVIEW_PAGE + 1})">›</button></div>`;
}
function reviewGotoPage(p) { REVIEW_PAGE = p; renderMain(); $('#main').scrollTop = 0; }
function reviewToggleUnanalyzed() { REVIEW_UNANALYZED_ONLY = !REVIEW_UNANALYZED_ONLY; REVIEW_PAGE = 0; renderMain(); }
function reviewToggleAllin() { REVIEW_HIDE_ALLIN = !REVIEW_HIDE_ALLIN; REVIEW_PAGE = 0; renderMain(); }

// 복기 필터(프리올인 숨기기 + 미분석만) 적용 — 페이징 전 목록. 렌더·배치가 공유해 일관성 유지.
function reviewFiltered() {
  let base = (REVIEW_HANDS || []).slice();
  if (REVIEW_HIDE_ALLIN) base = base.filter(h => h.pf_action !== 'allin');   // 히어로 프리플랍 올인 제외
  const totalUnan = base.filter(h => !reviewAnalyzed(h)).length;
  const list = REVIEW_UNANALYZED_ONLY ? base.filter(h => !reviewAnalyzed(h)) : base;
  return {list, totalUnan};
}

// 배치 진행 배너 (배치 중에만 표시) — 전체 재렌더 없이 이 요소만 갱신
function reviewBatchBanner() {
  const b = REVIEW_BATCH || {done: 0, total: 0};
  const pct = b.total ? Math.round(b.done / b.total * 100) : 0;
  const cur = b.currentId ? ` · 현재 #${b.currentId.slice(-6)}` : '';
  return `<div id="batch-banner" style="border:1px solid var(--border);border-radius:9px;padding:10px 14px;margin-bottom:12px;background:var(--panel);display:flex;align-items:center;gap:12px">
    <span>🤖 배치 분석 중 <b>${b.done}/${b.total}</b> (${pct}%)${cur}</span>
    <div style="flex:1;height:6px;background:var(--panel2);border-radius:3px;overflow:hidden;min-width:80px">
      <div style="width:${pct}%;height:100%;background:var(--accent)"></div></div>
    <button onclick="reviewStopBatch()">■ 중단</button></div>`;
}
function updateBatchProgress() {
  const el = document.getElementById('batch-banner');
  if (el && REVIEW_BATCH) el.outerHTML = reviewBatchBanner();
}
function reviewStopBatch() { if (REVIEW_BATCH) REVIEW_BATCH.stop = true; }

// 현재 페이지의 미분석 핸드만 순차 분석 (기존 /api/analyze 재사용, 각 핸드 완료 시 서버 저장 → 중단/재개 안전)
async function reviewAnalyzePage() {
  if (REVIEW_BATCH && REVIEW_BATCH.running) return;
  const page = reviewFiltered().list.slice(REVIEW_PAGE * REVIEW_PAGE_SIZE, (REVIEW_PAGE + 1) * REVIEW_PAGE_SIZE);
  const targets = page.filter(h => !reviewAnalyzed(h));
  if (!targets.length) { toast('이 페이지에 미분석 핸드가 없습니다'); return; }
  REVIEW_BATCH = {running: true, done: 0, total: targets.length, stop: false, currentId: null};
  renderMain();
  for (const h of targets) {
    if (REVIEW_BATCH.stop || SEL !== -1) break;
    REVIEW_BATCH.currentId = h.hand_id;
    updateBatchProgress();
    await analyzeHand(h.hand_id);                 // 스트리밍 + 서버 저장
    const c = AI_CACHE[h.hand_id];
    if (c && c.status === 'done') h.analysis = c.text;   // 로컬도 분석됨 표시(필터 일관성)
    REVIEW_BATCH.done++;
    updateBatchProgress();
  }
  const stopped = REVIEW_BATCH.stop;
  REVIEW_BATCH = null;
  if (SEL === -1) renderMain();
  toast(stopped ? '분석 중단됨' : '페이지 분석 완료');
}

// --- AI 분석 (스트리밍) ---
const AI_CACHE = {};  // hand_id -> {status: 'loading'|'streaming'|'done'|'error', text, backend}

// 분석 텍스트에서 전체 평가 이모지 추출
// 1순위: 총평의 "전체 평가: [X]" / 2순위: 스트리트 평가 중 최악 등급
const VERDICT_EMOJI = {'좋음': '✅', '무난': '🙂', '의문': '🤔', '실수': '❌'};
function verdictEmoji(text) {
  if (!text) return '';
  const overall = text.match(/전체\s*평가\s*[:：]\s*\[?(좋음|무난|의문|실수)\]?/);
  if (overall) return VERDICT_EMOJI[overall[1]];
  const found = [...text.matchAll(/\[(좋음|무난|의문|실수)\]/g)].map(m => m[1]);
  for (const v of ['실수', '의문', '무난', '좋음'])   // 최악 등급 우선
    if (found.includes(v)) return VERDICT_EMOJI[v];
  return '';
}

// 접힌 핸드 줄에 표시할 배지: 분석 완료 시 🤖 + 총평 이모지
function aiFlagHtml(handId) {
  const c = AI_CACHE[handId];
  if (!c) return '';
  // 접힌 줄에서도 진행 상태가 보이게: 분석중 🤖+초록스피너 · 에러 🤖⚠️ · 완료 🤖+등급
  if (c.status === 'loading' || c.status === 'streaming') return '🤖<span class="ai-spinner"></span>';
  if (c.status === 'error') return '🤖⚠️';
  if (c.status === 'done') return '🤖' + verdictEmoji(c.text);
  return '';
}

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
  const flag = document.getElementById('flag-' + handId);
  if (flag) flag.innerHTML = aiFlagHtml(handId);  // 접힌 줄 배지도 갱신
}

async function analyzeHand(handId) {
  let hand = (REVIEW_HANDS || []).find(h => h.hand_id === handId) || null;
  for (const t of DATA.tournaments) {
    if (hand) break;
    hand = (t.hands || []).find(h => h.hand_id === handId);
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
      LEAKS = null;   // 새 분석 반영되도록 리크 캐시 무효화
    }
  } catch (e) {
    AI_CACHE[handId] = {status: 'error', text: String(e)};
  }
  renderAIBox(handId);
}

// 토너먼트 선택 — 핸드는 처음 선택될 때 서버에서 지연 로드
async function selectTourney(i) {
  SEL = i; renderSidebar();
  const t = DATA.tournaments[i];
  if (!t) { $('#mainhead').innerHTML = ''; $('#hands').innerHTML = ''; return; }
  if (!t.hands) {
    $('#mainhead').innerHTML = `<h2>${esc(t.name)}</h2>`;
    $('#hands').innerHTML = '<div class="ai-loading">핸드 불러오는 중</div>';
    const res = await fetch('/api/tournament?id=' + encodeURIComponent(t.id));
    const data = await res.json();
    if (SEL !== i) return;  // 로딩 중 다른 토너먼트로 이동함
    t.hands = data.hands;
    for (const h of t.hands)
      if (h.analysis && !AI_CACHE[h.hand_id])
        AI_CACHE[h.hand_id] = {status: 'done', text: h.analysis, backend: '저장됨'};
  }
  renderMain(); $('#main').scrollTop = 0;
}
function toggleAll(open) {
  document.querySelectorAll('.hand').forEach(el => el.classList.toggle('open', open));
}

// 그리드 칸 클릭 → 해당 조합 핸드 목록 (현재 포지션·스택 필터 적용)
const STACK_LABEL = {pf: '<15bb', short: '15-25bb', mid: '25-40bb', deep: '40bb+'};
async function drillCombo(combo) {
  const pos = GRID_POS, stack = GRID_STACK;   // 그리드와 같은 필터로 좁혀서 조회
  const params = ['combo=' + encodeURIComponent(combo)];
  if (pos !== 'all') params.push('pos=' + encodeURIComponent(pos));
  if (stack !== 'all') params.push('stack=' + encodeURIComponent(stack));
  const filterLabel = [pos !== 'all' ? pos : null, stack !== 'all' ? STACK_LABEL[stack] : null]
    .filter(Boolean).join(' · ');
  const name = `🃏 ${combo}${filterLabel ? ' · ' + filterLabel : ''}`;
  SEL = -4; DRILL = null; renderSidebar();
  $('#mainhead').innerHTML = `<button onclick="backToGrid()">← 그리드로</button><h2>${esc(name)}</h2>`;
  $('#hands').innerHTML = '<div class="ai-loading">핸드 불러오는 중</div>';
  const data = await fetch('/api/handsby?' + params.join('&')).then(r => r.json());
  if (SEL !== -4) return;   // 로딩 중 다른 뷰로 이동함
  for (const h of data.hands)
    if (h.analysis && !AI_CACHE[h.hand_id])
      AI_CACHE[h.hand_id] = {status: 'done', text: h.analysis, backend: '저장됨'};
  DRILL = {id: 'drill', name, hand_count: data.hands.length, hands: data.hands};
  renderMain(); $('#main').scrollTop = 0;
}
function backToGrid() { STATS_TAB = 'grid'; selectStats(); }

// --- 💰 뱅크롤 (실제 돈 — 칩 EV와 별개 도메인) ---
async function selectBankroll() {
  SEL = -5; renderSidebar();
  $('#mainhead').innerHTML = '<h2>💰 뱅크롤</h2>';
  if (!BANKROLL) $('#hands').innerHTML = '<div class="ai-loading">집계 중</div>';
  const data = await fetch('/api/bankroll').then(r => r.json());
  if (SEL !== -5) return;
  BANKROLL = data; BANK_PAGE = 0; renderBankroll(); $('#main').scrollTop = 0;
}

// 뱅크롤 페이지네이션 (50개씩)
function bankPager(pages) {
  if (pages <= 1) return '';
  const cur = BANK_PAGE;
  const nums = [...new Set([0, pages - 1, cur - 1, cur, cur + 1])].filter(p => p >= 0 && p < pages).sort((a, b) => a - b);
  let html = `<div class="ts-pager"><button ${cur === 0 ? 'disabled' : ''} onclick="bankGotoPage(${cur - 1})">‹ 이전</button>`;
  let prev = -1;
  for (const p of nums) {
    if (prev >= 0 && p - prev > 1) html += `<span class="ts-ellip">…</span>`;
    html += `<button class="${p === cur ? 'primary' : ''}" onclick="bankGotoPage(${p})">${p + 1}</button>`;
    prev = p;
  }
  return html + `<button ${cur === pages - 1 ? 'disabled' : ''} onclick="bankGotoPage(${cur + 1})">다음 ›</button></div>`;
}
function bankGotoPage(p) { BANK_PAGE = p; renderBankroll(); $('#main').scrollTop = 0; }

function bankMoney(v, plus) {
  const c = v >= 0 ? 'var(--green)' : 'var(--red)';
  const s = v > 0 && plus ? '+' : (v < 0 ? '−' : '');
  return `<span style="color:${c}">${s}$${Math.abs(v).toFixed(2)}</span>`;
}
function bankAutoBuyin() {
  const m = ($('#bf-name').value || '').match(/₮\s*([0-9]+(?:\.[0-9]+)?)/);
  if (m) { $('#bf-buyin').value = parseFloat(m[1]); bankRecost(); }
}
function bankRecost() {
  const bi = +$('#bf-buyin').value || 0, en = +$('#bf-entries').value || 1;
  $('#bf-cost').value = (bi * en).toFixed(2);
}

// 바이인 추천 카드
function bankRecommend(b) {
  const r = b.recommendation;
  if (!r) return '';
  const colors = {up: 'var(--green)', stay: 'var(--accent)', caution: '#f0a500', down: 'var(--red)', neutral: 'var(--dim)'};
  const c = colors[r.level] || 'var(--dim)';
  let tierHtml = '';
  if (r.tier_from) {
    if (r.tier_to) {
      tierHtml = `<div style="font-size:26px;font-weight:800;color:${c};letter-spacing:0.03em;margin:8px 0 6px">
        ${esc(r.tier_from)}<span style="font-size:20px;margin:0 10px">→</span>${esc(r.tier_to)}
      </div>`;
    } else {
      tierHtml = `<div style="font-size:26px;font-weight:800;color:${c};letter-spacing:0.03em;margin:8px 0 6px">
        ${esc(r.tier_from)}
      </div>`;
    }
  }
  return `<div style="background:var(--panel);border:1px solid var(--border);border-radius:9px;padding:12px 14px;height:100%;box-sizing:border-box">
    <div style="display:flex;justify-content:space-between;align-items:center">
      <span style="font-size:12px;color:var(--dim)">💡 바이인 추천</span>
      <span style="font-weight:700;color:${c};font-size:13px">${esc(r.title)}</span>
    </div>
    ${tierHtml}
    <div style="font-size:12px;color:var(--dim);margin-bottom:3px">${esc(r.stats || '')}</div>
    <div style="font-size:13px;color:var(--text)">${esc(r.desc || '')}</div>
    ${r.next_step ? `<div style="font-size:12px;color:var(--accent);margin-top:6px">↗ ${esc(r.next_step)}</div>` : ''}
    ${r.warning ? `<div style="font-size:12px;color:var(--gold);margin-top:6px">⚠ ${esc(r.warning)}</div>` : ''}
  </div>`;
}

// 손익 차트 (누적 라인 ↔ 일별 막대 토글) — inline SVG
// ref = '진짜 손익'(현재잔고+총출금−총입금) 수평 기준선 값. null이면 안 그림.
// 토너손익선과 ref선의 갭 = 기록된 토너로 설명 안 되는 돈(캐시·보너스·누락 등).
function bankSparkBody(entries, ref) {
  if (entries.length < 2) return '';
  const ys = entries.map(e => e.cum_pnl);
  const hasRef = ref != null && isFinite(ref);
  const pool = hasRef ? [0, ref, ...ys] : [0, ...ys];
  const mn = Math.min(...pool), mx = Math.max(...pool), W = 800, H = 135, n = ys.length;
  const X = i => (i / (n - 1) * W).toFixed(1);
  const Y = v => (H - (v - mn) / ((mx - mn) || 1) * H).toFixed(1);
  const pts = ys.map((v, i) => `${X(i)},${Y(v)}`).join(' ');
  const last = ys[ys.length - 1];
  const refLine = hasRef
    ? `<line x1="0" y1="${Y(ref)}" x2="${W}" y2="${Y(ref)}" stroke="var(--gold)" stroke-width="1.5"
             stroke-dasharray="6 4" vector-effect="non-scaling-stroke"/>` : '';
  const refLbl = hasRef
    ? ` <span style="color:var(--gold)">· ┄ 진짜 손익 ${ref>=0?'+':'−'}$${Math.abs(ref).toFixed(2)}</span>` : '';
  return `<div style="color:var(--dim);font-size:12px;margin-bottom:4px">누적 손익 (${entries[0].date} ~ ${entries[n-1].date})${refLbl}</div>
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="width:100%;height:135px;display:block">
      <line x1="0" y1="${Y(0)}" x2="${W}" y2="${Y(0)}" stroke="var(--border)" stroke-width="1"/>
      ${refLine}
      <polyline points="${pts}" fill="none" stroke="${last>=0?'var(--green)':'var(--red)'}" stroke-width="2" vector-effect="non-scaling-stroke"/>
    </svg>`;
}
function bankDailyBody(daily) {
  if (!daily || !daily.length) return '';
  window.bankDailyData = daily;
  const W = 800, H = 135, n = daily.length;
  const vals = daily.map(d => d.pnl);
  const mx = Math.max(0, ...vals), mn = Math.min(0, ...vals);
  const range = (mx - mn) || 1, zeroY = H * mx / range, bw = W / n;
  const bars = daily.map((d, i) => {
    const h = Math.abs(d.pnl) / range * H;
    const y = (d.pnl >= 0 ? zeroY - h : zeroY).toFixed(1);
    const col = d.pnl >= 0 ? 'var(--green)' : 'var(--red)';
    return `<rect x="${(i*bw).toFixed(1)}" y="${y}" width="${Math.max(0.6, bw*0.8).toFixed(1)}" height="${Math.max(0.6, h).toFixed(1)}" fill="${col}"/>`;
  }).join('');
  return `<div style="color:var(--dim);font-size:12px;margin-bottom:4px">일별 손익 (${daily[0].date} ~ ${daily[n-1].date})</div>
    <div style="position:relative">
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="width:100%;height:135px;display:block"
           onmousemove="bankDailyHover(event)" onmouseleave="bankDailyHide()">
        <line x1="0" y1="${zeroY.toFixed(1)}" x2="${W}" y2="${zeroY.toFixed(1)}" stroke="var(--border)" stroke-width="1"/>
        ${bars}
      </svg>
      <div id="bankDaily_tip" style="position:absolute;display:none;pointer-events:none;background:var(--panel2);border:1px solid var(--border);border-radius:5px;padding:3px 8px;font-size:11px;white-space:nowrap;z-index:20;box-shadow:0 2px 6px rgba(0,0,0,.3)"></div>
    </div>`;
}
function bankDailyHover(e) {
  const d = window.bankDailyData; if (!d || !d.length) return;
  const r = e.currentTarget.getBoundingClientRect();
  const idx = Math.max(0, Math.min(d.length - 1, Math.floor((e.clientX - r.left) / r.width * d.length)));
  const day = d[idx];
  const tip = document.getElementById('bankDaily_tip'); if (!tip) return;
  tip.innerHTML = `${day.date} · <b style="color:${day.pnl>=0?'var(--green)':'var(--red)'}">${day.pnl>=0?'+':''}$${day.pnl.toFixed(2)}</b>`;
  tip.style.display = 'block';
  const x = e.clientX - r.left, tw = tip.offsetWidth;
  let left = x + 12;
  if (left + tw > r.width) left = x - tw - 12;     // 커서가 오른쪽이면 툴팁을 왼쪽으로 뒤집어 안 가리게
  tip.style.left = Math.max(2, left) + 'px';
  tip.style.top = Math.max(2, e.clientY - r.top - 6) + 'px';
}
function bankDailyHide() { const t = document.getElementById('bankDaily_tip'); if (t) t.style.display = 'none'; }
function bankChartCol(b) {
  // 진짜 손익 = 현재잔고 + 총출금 − 총입금 (잔고 입력돼 있을 때만). 누적 차트에 기준선으로.
  const ref = (b.balance && b.balance.balance != null)
    ? b.balance.balance + b.cf_withdraw - b.cf_deposit : null;
  const cumBody = bankSparkBody(b.entries, ref), dayBody = bankDailyBody(b.daily);
  if (!cumBody && !dayBody) return '';
  const body = BANK_CHART === 'daily' ? (dayBody || cumBody) : (cumBody || dayBody);
  const tbtn = (m, l) => `<button class="${BANK_CHART===m?'primary':''}" onclick="bankSetChart('${m}')" style="font-size:11px;padding:2px 9px">${l}</button>`;
  return `<div style="background:var(--panel);border:1px solid var(--border);border-radius:9px;padding:10px 12px;height:100%;box-sizing:border-box">
    <div style="display:flex;gap:5px;margin-bottom:6px">${tbtn('cum','누적')}${tbtn('daily','일별')}</div>
    ${body}
  </div>`;
}

function bankForm() {
  const fv = BANK_EDIT ? (BANKROLL.entries.find(e => e.id === BANK_EDIT) || {}) : (BANK_PREFILL || {});
  const v = (k, d) => fv[k] !== undefined && fv[k] !== null ? esc(String(fv[k])) : (d || '');
  const inp = (id, ph, val, extra = '') => `<input id="bf-${id}" placeholder="${ph}" value="${val}" ${extra}
     style="background:var(--panel2);border:1px solid var(--border);color:var(--text);border-radius:6px;padding:6px 8px;font-size:13px">`;
  return `<div style="background:var(--panel);border:1px solid var(--accent);border-radius:9px;padding:14px;margin-bottom:14px">
    <input type="hidden" id="bf-id" value="${BANK_EDIT || ''}">
    <div style="font-weight:600;margin-bottom:10px">${BANK_EDIT ? '✏️ 결과 수정' : '➕ 토너 결과 입력'}</div>
    <div style="display:grid;grid-template-columns:130px 1fr;gap:8px;align-items:center;max-width:640px">
      <label class="small">날짜</label>${inp('date', 'YYYY-MM-DD', v('date'), 'type="date"')}
      <label class="small">토너먼트명</label>${inp('name', '예: ₮5.50 Turbo', v('name'), 'oninput="bankAutoBuyin()"')}
      <label class="small">바이인 ($)</label>${inp('buyin', '0', v('buyin'), 'type="number" step="0.01" oninput="bankRecost()"')}
      <label class="small">바이인/리바이 횟수</label>${inp('entries', '1', v('entries', '1'), 'type="number" min="1" oninput="bankRecost()"')}
      <label class="small">총 비용 ($)</label>${inp('cost', '자동', v('cost'), 'type="number" step="0.01"')}
      <label class="small">상금 ($)</label>${inp('cash', '0', v('cash'), 'type="number" step="0.01"')}
      <label class="small">순위</label>${inp('rank', '선택', v('rank'))}
      <label class="small">메모</label>${inp('memo', '선택', v('memo'))}
    </div>
    <div style="margin-top:12px;display:flex;gap:8px">
      <button class="primary" onclick="bankSave()">${BANK_EDIT ? '저장' : '추가'}</button>
      <button onclick="bankCancel()">취소</button>
    </div>
  </div>`;
}
function bankShowForm() { BANK_SHOWFORM = true; BANK_EDIT = null; BANK_PREFILL = null; renderBankroll(); }
function bankCancel() { BANK_SHOWFORM = false; BANK_EDIT = null; BANK_PREFILL = null; renderBankroll(); }
function bankEdit(id) { BANK_EDIT = id; BANK_SHOWFORM = true; BANK_PREFILL = null; renderBankroll(); $('#main').scrollTop = 0; }
function bankPrefill(name, date, buyin) {
  BANK_PREFILL = {name, date, buyin}; BANK_SHOWFORM = true; BANK_EDIT = null; renderBankroll(); $('#main').scrollTop = 0;
}
async function bankSave() {
  const num = id => $('#bf-' + id).value === '' ? undefined : +$('#bf-' + id).value;
  const body = {
    id: $('#bf-id').value || undefined,
    date: $('#bf-date').value, name: $('#bf-name').value.trim(),
    buyin: num('buyin'), entries: num('entries'), cost: num('cost'),
    cash: num('cash') || 0, rank: $('#bf-rank').value, memo: $('#bf-memo').value,
  };
  if (!body.name) { toast('토너먼트명을 입력하세요'); return; }
  const data = await fetch('/api/bankroll/entry', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
  }).then(r => r.json());
  BANKROLL = data; BANK_SHOWFORM = false; BANK_EDIT = null; BANK_PREFILL = null;
  renderBankroll(); toast(body.id ? '수정됨' : '추가됨');
}
async function bankConfirm(id) {
  const data = await fetch('/api/bankroll/confirm', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id}),
  }).then(r => r.json());
  BANKROLL = data; renderBankroll(); toast('확인됨');
}
async function bankDelete(id) {
  if (!confirm('이 기록을 삭제할까요?')) return;
  const data = await fetch('/api/bankroll/delete', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id}),
  }).then(r => r.json());
  BANKROLL = data; renderBankroll(); toast('삭제됨');
}
function bankSetFilter(f) { BANK_FILTER = f; BANK_PAGE = 0; renderBankroll(); }
function bankSetChart(m) { BANK_CHART = m; renderBankroll(); }

// 실제 잔고 스냅샷 입력 — 이후 토너 손익은 자동 추적, 리워드 등 차이는 재입력으로 보정
async function bankSetBalance() {
  const cur = (BANKROLL.balance && BANKROLL.balance.balance != null) ? BANKROLL.balance.balance : '';
  const v = prompt('현재 실제 사이트 잔고($)를 입력하세요.\n\n· 이후 토너 손익은 자동으로 더하고 뺍니다.\n· 레이크백·리더보드 등 토너 밖 수입으로 실제와 차이가 나면, 가끔 다시 입력해 보정하세요.', cur);
  if (v === null) return;
  const amount = parseFloat(v);
  if (isNaN(amount)) { toast('숫자를 입력하세요'); return; }
  const data = await fetch('/api/bankroll/balance', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({amount}),
  }).then(r => r.json());
  BANKROLL = data; renderBankroll(); toast('잔고 저장됨');
}

// 입출금 기록 — 토너 손익과 별개 원장. 출금하면 잔고↓ → 바이인 추천도 낮아짐(의도된 동작).
// 출금 method: 'wallet'(내 지갑, $5 수수료) / 'transfer'(유저 송금, 수수료 없음).
async function bankAddCashflow(type, method) {
  const label = type === 'withdraw'
    ? (method === 'transfer' ? '유저 송금' : '지갑 출금') : '입금';
  let hint;
  if (type !== 'withdraw') hint = '입금분만큼 잔고가 늘어 추천 바이인에 반영됩니다.';
  else if (method === 'wallet') hint = '내 지갑으로 이체. 출금액에 네트워크 수수료 $5가 포함됩니다 — 잔고는 입력 금액만큼 줄고, 실수령은 (금액−$5)입니다.';
  else hint = '다른 유저에게 송금. 수수료 없이 입력 금액만큼 잔고가 줄어듭니다(대가는 앱 밖에서 직접 수령).';
  const v = prompt(`${label} 금액($, 계좌에서 빠지는 총액)을 입력하세요.\n\n· 토너 손익(ROI)에는 섞이지 않습니다.\n· ${hint}`, '');
  if (v === null) return;
  const amount = parseFloat(v);
  if (isNaN(amount) || amount <= 0) { toast('0보다 큰 숫자를 입력하세요'); return; }
  const date = prompt('날짜 (YYYY-MM-DD)', new Date().toISOString().slice(0,10));
  if (date === null) return;
  const note = prompt('메모 (선택)', '') || '';
  const data = await fetch('/api/bankroll/cashflow', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({type, method, amount, date, note}),
  }).then(r => r.json());
  if (data.error) { toast(data.error); return; }
  BANKROLL = data; renderBankroll(); toast(`${label} 기록됨`);
}
async function bankDelCashflow(id) {
  if (!confirm('이 입출금 기록을 삭제할까요?')) return;
  const data = await fetch('/api/bankroll/cashflow/delete', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id}),
  }).then(r => r.json());
  BANKROLL = data; renderBankroll(); toast('삭제됨');
}

// 뱅크롤 행 클릭 → 그 토너 핸드 보기 (이미 있는 토너 뷰 재사용)
function openTournamentById(tid) {
  const i = DATA.tournaments.findIndex(t => t.id === tid);
  if (i >= 0) selectTourney(i); else toast('연결된 핸드가 없습니다');
}

// 캠페인 트리: 부모(본토너/최고단계)는 토글, 자식(세틀)은 기본 접힘. 각 행은 제 숫자만(#2 후자).
function bankRowTr(e, opts) {
  opts = opts || {};
  const badge = e.tournament_id
    ? `<span style="color:var(--accent);cursor:pointer" onclick="openTournamentById('${e.tournament_id}')">${e.hands}핸드 ›</span>`
    : `<span style="color:var(--gold)" title="연결된 핸드 없음">핸드없음</span>`;
  let name;
  if (opts.nKids) {
    name = `<span id="tw-${opts.campId}" onclick="bankToggle('${opts.campId}')" style="cursor:pointer;color:var(--dim);user-select:none;margin-right:5px">▶</span>${esc(e.name)}<span style="color:var(--dim);font-size:11px"> · 세틀 ${opts.nKids}</span>`;
  } else if (opts.isChild) {
    name = `<span style="color:var(--dim);margin-left:16px">└ ${esc(e.name)}</span>`;
  } else {
    name = esc(e.name);
  }
  const oc = e.is_sat && e.outcome ? (e.outcome === 'won'
    ? ` <span style="color:var(--green);font-size:11px" title="세틀에서 살아남아 시트 획득(핸드 판정)">🎟 시트</span>`
    : ` <span style="color:var(--dim);font-size:11px" title="세틀에서 버스트(핸드 판정)">버스트</span>`) : '';
  const extra = `${e.entries>1?` <span style="color:var(--dim)">×${e.entries}</span>`:''}${e.rank?` <span style="color:var(--dim)">${esc(e.rank)}</span>`:''}`;
  const hide = opts.isChild ? 'display:none;background:rgba(0,0,0,.15);' : '';
  const pending = e.confirmed === false;        // 신규 자동등록 → 확인 대기
  // 맨 왼쪽: 상태 표시 전용 칸 (지금은 확인 대기 태그, 향후 다른 뱃지도 여기)
  const statusCell = `<td style="padding:6px 6px;white-space:nowrap">${pending?'<span style="color:var(--gold);font-size:11px">⚠ 확인 필요</span>':''}</td>`;
  const confirmBtn = pending
    ? `<a href="#" title="확인 (버스트 등 상금 입력 불필요)" style="color:var(--green);font-size:14px;margin-right:8px" onclick="event.preventDefault();bankConfirm('${e.id}')">✓</a>`
    : '';
  return `<tr class="${opts.isChild?('kid-'+opts.kidOf):''}" style="border-bottom:1px solid var(--border);${hide}">
    ${statusCell}
    <td style="padding:6px 8px;white-space:nowrap;color:var(--dim)">${esc(e.date || '')}</td>
    <td style="padding:6px 8px">${name}${oc}${extra}</td>
    <td style="padding:6px 8px;text-align:right;color:var(--dim)">$${e.cost.toFixed(2)}</td>
    <td style="padding:6px 8px;text-align:right">${e.cash?('$'+e.cash.toFixed(2)):'<span style="color:var(--dim)">-</span>'}</td>
    <td style="padding:6px 8px;text-align:right;font-weight:600">${bankMoney(e.pnl, true)}</td>
    <td style="padding:6px 8px;text-align:right;font-size:12px">${badge}</td>
    <td style="padding:6px 8px;text-align:right;white-space:nowrap">
      ${confirmBtn}
      <a href="#" title="수정" style="font-size:14px;text-decoration:none" onclick="event.preventDefault();bankEdit('${e.id}')">✏️</a>
      <a href="#" title="삭제" style="font-size:14px;text-decoration:none;margin-left:8px" onclick="event.preventDefault();bankDelete('${e.id}')">🗑️</a>
    </td></tr>`;
}
function bankToggle(id) {
  const kids = document.querySelectorAll('.kid-' + id);
  const tw = document.getElementById('tw-' + id);
  const open = tw.textContent === '▼';
  kids.forEach(k => k.style.display = open ? 'none' : 'table-row');
  tw.textContent = open ? '▶' : '▼';
}

function bankSetTab(t) { BANK_TAB = t; renderBankroll(); $('#main').scrollTop = 0; }
function bankCfGotoPage(p) { BANK_CF_PAGE = p; renderBankroll(); $('#main').scrollTop = 0; }

// 뱅크롤 = 한 페이지 안의 2탭: 📊 토너 성적(플레이 결과) / 💸 입출금(실제 돈). 데이터는 /api/bankroll 한 방.
function renderBankroll() {
  const b = BANKROLL;
  const isCash = BANK_TAB === 'cash';
  const headBtn = isCash
    ? `<button class="primary" style="margin-left:auto" onclick="bankAddCashflow('deposit')">＋ 입금</button>`
    : `<button class="primary" style="margin-left:auto" onclick="bankShowForm()">➕ 결과 입력</button>`;
  $('#mainhead').innerHTML = `<h2 style="flex:0 0 auto">💰 뱅크롤</h2>${headBtn}`;
  const tab = (k, l) => `<button class="${BANK_TAB===k?'primary':''}" onclick="bankSetTab('${k}')">${l}</button>`;
  const tabBar = `<div style="display:flex;gap:6px;margin-bottom:14px;border-bottom:1px solid var(--border);padding-bottom:10px">
    ${tab('results','📊 토너 성적')} ${tab('cash','💸 입출금')}</div>`;
  $('#hands').innerHTML = tabBar + (isCash ? bankCashTab(b) : bankResultsTab(b));
}

// ── 탭 1: 토너 성적 (칩→돈 플레이 품질) ─────────────────────────────
function bankResultsTab(b) {
  const cards = `<div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px">
    ${statCard(bankMoney(b.profit, true), '순손익', `비용 $${b.total_cost} · 상금 $${b.total_cash}`)}
    ${statCard((b.roi>=0?'+':'') + b.roi + '%', 'ROI', '상금/비용')}
    ${statCard(b.itm_pct + '%', 'ITM', `상금권 ${b.n_paid}토너 중`)}
    ${statCard(b.n, '토너 수', `평균 바이인 $${b.avg_buyin}`)}
    ${statCard('$' + b.biggest_cash.toFixed(2), '최고 상금', '단일 토너')}
  </div>`;

  const fBtn = (k, l) => `<button class="${BANK_FILTER===k?'primary':''}" onclick="bankSetFilter('${k}')">${l}</button>`;
  // 페이징 대상: 트리는 루트, 필터는 평면 엔트리 (50개씩)
  const isTree = BANK_FILTER === 'all';
  let items = isTree ? b.tree : b.entries.slice().reverse();
  if (BANK_FILTER === 'unmatched') items = items.filter(e => !e.tournament_id);
  else if (BANK_FILTER === 'itm') items = items.filter(e => e.cash > 0);
  // 평면 뷰도 확인 대기(미확인)를 상단으로 (트리는 백엔드에서 이미 정렬). stable sort라 그 외 순서 유지
  if (!isTree) items = items.slice().sort((a, b) => (a.confirmed===false?0:1) - (b.confirmed===false?0:1));
  const total = items.length;
  const pages = Math.max(1, Math.ceil(total / BANK_PAGE_SIZE));
  if (BANK_PAGE >= pages) BANK_PAGE = pages - 1;
  if (BANK_PAGE < 0) BANK_PAGE = 0;
  const pageItems = items.slice(BANK_PAGE * BANK_PAGE_SIZE, (BANK_PAGE + 1) * BANK_PAGE_SIZE);
  const bodyRows = isTree
    ? pageItems.map(n => {
        const kids = n.children || [];
        return bankRowTr(n, kids.length ? {campId: n.id, nKids: kids.length} : {})
          + kids.map(c => bankRowTr(c, {isChild: true, kidOf: n.id})).join('');
      }).join('')
    : pageItems.map(e => bankRowTr(e, {})).join('');
  const countLabel = (isTree ? `${total}개 캠페인` : `${total}건`)
    + (pages > 1 ? ` · ${BANK_PAGE + 1}/${pages}p` : '');
  const table = `<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:13px">
    <tr style="color:var(--dim);text-align:left;border-bottom:1px solid var(--border)">
      <th style="padding:6px 6px"></th>
      <th style="padding:6px 8px">날짜</th><th style="padding:6px 8px">토너먼트</th>
      <th style="padding:6px 8px;text-align:right">비용</th><th style="padding:6px 8px;text-align:right">상금</th>
      <th style="padding:6px 8px;text-align:right">손익</th>
      <th style="padding:6px 8px;text-align:right">핸드</th><th></th></tr>
    ${bodyRows}</table></div>`;

  // 역방향: 핸드는 있는데 기록 없는 유료 토너 ($0 프리롤 제외).
  // 티켓 입장(세틀에서 올라옴)은 버스트면 기록 불필요(돈 누락 아님) → 별도 그룹으로, 바이인 0 프리필.
  const ulPaid = b.unlogged.filter(u => u.buyin > 0);
  const ulTicket = ulPaid.filter(u => u.ticket);
  const ulReal = ulPaid.filter(u => !u.ticket);
  const ulRow = (u, prefBuyin) => `
        <div style="display:flex;gap:10px;align-items:center;padding:4px 0;border-bottom:1px solid var(--border)">
          <span style="color:var(--dim);width:90px">${esc(u.start)}</span>
          <span style="flex:1">${esc(u.name)}</span>
          <span style="color:var(--dim)">${u.hands}핸드</span>
          <button onclick="bankPrefill('${esc(u.name).replace(/'/g,'')}','${u.start}',${prefBuyin})">+ 본토너 기록</button>
        </div>`;
  const ulTicketBlock = ulTicket.length ? `
    <details style="margin-top:18px"><summary style="cursor:pointer;color:var(--green)">🎟 티켓 입장 (세틀에서 올라옴 · 버스트면 기록 불필요) ${ulTicket.length}개</summary>
      <div style="margin-top:8px;font-size:12px;color:var(--dim)">바이인 0(티켓)으로 채워집니다. ITM 했던 것만 상금 입력해 누락분 보정하세요.</div>
      <div style="margin-top:6px;font-size:13px">${ulTicket.slice(0,60).map(u => ulRow(u, 0)).join('')}</div></details>` : '';
  const ulRealBlock = ulReal.length ? `
    <details style="margin-top:14px"><summary style="cursor:pointer;color:var(--gold)">⚠ 핸드는 있는데 기록 없는 현금 토너 ${ulReal.length}개 (점검)</summary>
      <div style="margin-top:8px;font-size:13px">${ulReal.slice(0,60).map(u => ulRow(u, u.buyin)).join('')}</div></details>` : '';
  const unlogged = ulTicketBlock + ulRealBlock;

  const unmatchedNote = b.unmatched.length
    ? `<div style="color:var(--dim);font-size:12px;margin:10px 0">매칭 안 된 ${b.unmatched.length}건은 '핸드없음'으로 표시 — 새틀라이트/PLO/미기록 핸드라 정상입니다. 손익 합계엔 모두 포함됩니다.</div>`
    : '';

  const form = BANK_SHOWFORM ? bankForm() : '';
  // 누적 손익 차트(왼쪽 절반) + 바이인 추천(오른쪽 남는 공간) 나란히 배치
  const chartCol = bankChartCol(b), rec = bankRecommend(b);
  const chartRow = (chartCol && rec)
    ? `<div style="display:flex;gap:14px;margin-bottom:14px;align-items:stretch">
         <div style="flex:1;min-width:0;display:flex;flex-direction:column">${chartCol}</div>
         <div style="flex:1;min-width:0;display:flex;flex-direction:column">${rec}</div></div>`
    : (chartCol || rec
        ? `<div style="margin-bottom:14px">${chartCol || rec}</div>`
        : '');
  return cards + chartRow + form
    + `<div style="display:flex;gap:6px;margin-bottom:8px"><span style="color:var(--dim);font-size:13px;align-self:center">보기:</span>
       ${fBtn('all','캠페인 트리')} ${fBtn('itm','ITM만')} ${fBtn('unmatched','미매칭만')}
       <span style="margin-left:auto;color:var(--dim);font-size:12px;align-self:center">${countLabel} 표시</span></div>`
    + unmatchedNote + table + bankPager(pages) + unlogged;
}

// ── 탭 2: 입출금 (실제 돈의 입/출, 잔고) ─────────────────────────────
function bankCashTab(b) {
  const bal = b.balance;
  const balVal = bal ? '$' + bal.balance.toFixed(2) : '<span style="color:var(--dim);font-size:18px">입력 →</span>';
  const cfStr = bal && bal.since_cashflow ? ` · 입출금 ${bal.since_cashflow>=0?'+':''}${bal.since_cashflow.toFixed(2)}` : '';
  // 앵커 시각이 그날 끝(23:59:59)이면 소급/구버전 스냅샷 → 날짜만, 아니면 분까지 표시
  const anchorLbl = bal ? (/23:59:59$/.test(bal.anchor_at||'') ? bal.anchor_date : (bal.anchor_at||'').slice(0,16)) : '';
  const balSub = bal
    ? `${anchorLbl} 기준 ${bal.since_pnl>=0?'+':''}${bal.since_pnl.toFixed(2)}${cfStr}`
    : '실제 사이트 잔고 클릭 입력';
  const balCard = `<div onclick="bankSetBalance()" style="cursor:pointer" title="실제 사이트 잔고 입력/보정 — 토너 손익·입출금은 자동 추적됩니다">${statCard(balVal, '💵 현재 잔고', balSub)}</div>`;
  // 순 회수 = 출금 − 입금 (내 주머니 관점: 출금은 +, 입금은 −). 양수면 넣은 것보다 더 빼낸 것.
  const net = (b.cf_withdraw - b.cf_deposit);

  // ── 총수익 히어로 배너 = 현재잔고 + 총출금 − 총입금 (진짜 현금 손익) ──
  const balNum = bal ? bal.balance : null;
  let hero;
  if (balNum == null) {
    hero = `<div style="border:1px solid var(--border);border-radius:10px;padding:14px 18px;margin-bottom:14px;background:var(--panel);display:flex;align-items:center;gap:12px;cursor:pointer" onclick="bankSetBalance()">
      <span style="font-size:15px;color:var(--dim)">💰 총수익</span>
      <span style="margin-left:auto;color:var(--accent);font-size:14px">현재 잔고를 입력하면 계산됩니다 →</span></div>`;
  } else {
    const profit = balNum + b.cf_withdraw - b.cf_deposit;
    const pos = profit >= 0;
    const col = pos ? 'var(--green)' : 'var(--red)';
    const warn = (b.cf_deposit === 0)
      ? ` <span style="color:var(--gold);font-size:13px;cursor:help" title="입금 기록이 없어요. 처음부터의 입금을 모두 기록해야 총수익이 정확합니다.">⚠ 입금 기록 필요</span>` : '';
    hero = `<div style="border:1px solid var(--border);border-radius:10px;padding:16px 20px;margin-bottom:14px;background:var(--panel)">
      <div style="display:flex;align-items:baseline;gap:14px;flex-wrap:wrap">
        <span style="font-size:15px;color:var(--dim)">💰 총수익</span>
        <span style="font-size:30px;font-weight:700;color:${col};font-variant-numeric:tabular-nums">${pos?'+':'−'}$${Math.abs(profit).toFixed(2)}</span>${warn}
      </div>
      <div style="margin-top:6px;color:var(--dim);font-size:13px">현재잔고 $${balNum.toFixed(2)} + 총출금 $${b.cf_withdraw.toFixed(2)} − 총입금 $${b.cf_deposit.toFixed(2)}</div>
    </div>`;
  }

  const cards = `<div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:6px">
    ${balCard}
    ${statCard('$' + b.cf_deposit.toFixed(2), '총 입금', '계좌로 넣은 돈')}
    ${statCard('$' + b.cf_withdraw.toFixed(2), '총 출금', '계좌에서 뺀 돈')}
    ${statCard((net>=0?'+':'−') + '$' + Math.abs(net).toFixed(2), '순 회수', '출금 − 입금')}
    ${statCard('$' + (b.cf_fee||0).toFixed(2), '수수료 합계', '지갑 출금 네트워크비')}
  </div>`;
  const balHint = `<div style="color:var(--dim);font-size:12px;margin-bottom:14px">현재 잔고 카드를 눌러 실제 사이트 잔고를 보정할 수 있어요. 토너 손익·입출금은 그 기준 이후 자동 반영됩니다.</div>`;

  const addBtns = `<div style="margin:4px 0 6px;display:flex;gap:6px;flex-wrap:wrap">
      <button class="primary" onclick="bankAddCashflow('deposit')">＋ 입금</button>
      <button onclick="bankAddCashflow('withdraw','wallet')">－ 지갑 출금 ($5 수수료)</button>
      <button onclick="bankAddCashflow('withdraw','transfer')">－ 유저 송금 (수수료 없음)</button>
    </div>`;
  const note = `<div style="font-size:12px;color:var(--dim);margin-bottom:10px">출금하면 위험 자본이 줄어 추정 잔고·추천 바이인이 함께 내려갑니다 — 딴 돈을 지키는 정상 동작입니다. 지갑 출금은 입력 금액에 네트워크 수수료 $5가 포함됩니다(실수령 = 금액−$5).</div>`;

  const cfs = (b.cashflows || []).slice().reverse();
  const pages = Math.max(1, Math.ceil(cfs.length / BANK_PAGE_SIZE));
  if (BANK_CF_PAGE >= pages) BANK_CF_PAGE = pages - 1;
  if (BANK_CF_PAGE < 0) BANK_CF_PAGE = 0;
  const pageItems = cfs.slice(BANK_CF_PAGE * BANK_PAGE_SIZE, (BANK_CF_PAGE + 1) * BANK_PAGE_SIZE);
  const rows = pageItems.map(c => {
    const isW = c.type === 'withdraw';
    const sign = isW ? '−' : '+';
    const col = isW ? 'var(--gold)' : 'var(--green)';
    const kind = isW ? (c.method === 'transfer' ? '유저 송금' : '지갑 출금') : '입금';
    const sub = (isW && c.fee) ? `실수령 $${(c.amount-c.fee).toFixed(2)} · 수수료 $${c.fee.toFixed(2)}` : '';
    return `<tr style="border-bottom:1px solid var(--border)">
        <td style="padding:6px 8px;color:var(--dim)">${esc(c.date||'—')}</td>
        <td style="padding:6px 8px;color:${col}">${kind}</td>
        <td style="padding:6px 8px;text-align:right;color:${col};font-variant-numeric:tabular-nums">${sign}$${c.amount.toFixed(2)}</td>
        <td style="padding:6px 8px;color:var(--dim);font-size:12px">${sub}</td>
        <td style="padding:6px 8px;color:var(--dim)">${esc(c.note||'')}</td>
        <td style="padding:6px 8px;text-align:right"><span style="cursor:pointer;color:var(--dim)" title="삭제" onclick="bankDelCashflow('${c.id}')">🗑</span></td>
      </tr>`;
  }).join('');
  const table = cfs.length
    ? `<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:13px">
        <tr style="color:var(--dim);text-align:left;border-bottom:1px solid var(--border)">
          <th style="padding:6px 8px">날짜</th><th style="padding:6px 8px">구분</th>
          <th style="padding:6px 8px;text-align:right">금액</th><th style="padding:6px 8px">실수령/수수료</th>
          <th style="padding:6px 8px">메모</th><th></th></tr>
        ${rows}</table></div>`
    : `<div style="color:var(--dim);font-size:13px;padding:14px 0;text-align:center">아직 입출금 기록이 없습니다. 위 버튼으로 추가하세요.</div>`;
  const pager = pages > 1
    ? `<div style="display:flex;gap:6px;justify-content:center;margin-top:10px">
        <button ${BANK_CF_PAGE<=0?'disabled':''} onclick="bankCfGotoPage(${BANK_CF_PAGE-1})">‹</button>
        <span style="align-self:center;color:var(--dim);font-size:12px">${BANK_CF_PAGE+1}/${pages}p · ${cfs.length}건</span>
        <button ${BANK_CF_PAGE>=pages-1?'disabled':''} onclick="bankCfGotoPage(${BANK_CF_PAGE+1})">›</button></div>`
    : '';

  return hero + cards + balHint + addBtns + note + table + pager;
}

function tourneyMd() {
  // 필터가 켜져 있으면 표시 중인 핸드만 복사/다운로드
  return visibleHands().map(h => h.markdown).join('\n---\n\n');
}
function copyMd() {
  navigator.clipboard.writeText(tourneyMd()).then(() => toast('복사 완료 — AI에게 붙여넣으세요'));
}
function downloadMd() {
  const t = currentTourney();
  const blob = new Blob([tourneyMd()], {type: 'text/markdown'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `tournament_${t.id}.md`;
  a.click();
}

async function applyData(data) {
  DATA = data; SEL = 0;
  REPORT = data.report || null;
  ANALYZED_TOTAL = data.analyzed_total || 0;
  REVIEW_COUNT = data.review_count || 0;
  REVIEW_HANDS = null; STATS = null; LEAKS = null; GRID_CACHE = {}; GRID_POS = 'all'; GRID_STACK = 'all';  // 임포트 후 다시 로드되도록 초기화
  updateReportBtn();
  const params = new URLSearchParams(location.search);
  HIDE_FOLDS = params.has('hidefolds');
  $('#drop').style.display = 'none';
  $('#layout').classList.add('active');
  if (params.has('review')) await selectReview();
  else if (params.has('search')) selectSearch();
  else await selectStats();
}

async function importText(text) {
  const hero = 'Hero';   // CoinPoker는 본인을 항상 'Hero'로 익명화 (전 핸드 100% 확인)
  const res = await fetch('/api/import?hero=' + encodeURIComponent(hero), {
    method: 'POST', body: text,
  });
  const data = await res.json();
  if (data.error) { toast(data.error); return; }
  applyData(data);
  toast(`신규 ${data.added}개 추가 · 기존 ${data.skipped}개 스킵`
    + (data.bankroll_added ? ` · 뱅크롤 ${data.bankroll_added}토너 추가(상금 입력 필요)` : ''));
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

// --- 종합 리포트 ---
function updateReportBtn() {
  const b = $('#btnReport');
  b.style.display = '';
  b.textContent = `📊 종합 리포트${ANALYZED_TOTAL ? ` (${ANALYZED_TOTAL}핸드 분석됨)` : ''}`;
}
function openReport() { $('#report-overlay').classList.add('open'); renderReport(); }
function closeReport() { $('#report-overlay').classList.remove('open'); }

function renderReport(streamText) {
  const el = $('#report-body');
  if (REPORT_STREAMING) {
    el.innerHTML = mdToHtml(streamText || '') + '<span class="ai-cursor">▍</span>';
    return;
  }
  if (REPORT) {
    el.innerHTML = mdToHtml(REPORT.text) +
      `<div class="ai-meta">${esc(REPORT.created_at)} 생성 · 분석 핸드 ${REPORT.hand_count}개 기반 · ` +
      `<a href="#" style="color:var(--dim)" onclick="event.preventDefault(); generateReport()">다시 생성</a></div>`;
  } else {
    el.innerHTML = `
      <p style="color:var(--dim)">분석된 핸드들을 모아 반복되는 실수 패턴을 진단합니다.<br>
      현재 분석된 핸드: ${ANALYZED_TOTAL}개 (3개 이상 필요)</p>
      <button class="primary" style="margin-top:12px" onclick="generateReport()">리포트 생성</button>`;
  }
}

async function generateReport() {
  if (REPORT_STREAMING) return;
  REPORT_STREAMING = true;
  renderReport('');
  try {
    const res = await fetch('/api/report', {method: 'POST'});
    if (!res.ok) {
      const data = await res.json();
      REPORT_STREAMING = false;
      $('#report-body').innerHTML = `<div class="ai-error">${esc(data.error || 'HTTP ' + res.status)}</div>`;
      return;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let text = '';
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      text += decoder.decode(value, {stream: true});
      renderReport(text);
    }
    text += decoder.decode();
    REPORT_STREAMING = false;
    if (text.trim()) {
      const now = new Date();
      REPORT = {text, created_at: now.toISOString().slice(0,16).replace('T',' '),
                hand_count: ANALYZED_TOTAL};
    }
    renderReport();
  } catch (e) {
    REPORT_STREAMING = false;
    $('#report-body').innerHTML = `<div class="ai-error">${esc(String(e))}</div>`;
  }
}

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
        from urllib.parse import parse_qs, urlparse
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(INDEX_HTML)
        elif path == "/api/db":
            resp = store.tournament_list(DB)
            resp["report"] = DB.get("report")
            resp["analyzed_total"] = sum(1 for r in DB["hands"].values() if r.get("analysis"))
            resp["review_count"] = sum(1 for r in DB["hands"].values() if r.get("review"))
            self._send(json.dumps(resp, ensure_ascii=False), "application/json; charset=utf-8")
        elif path == "/api/review":
            resp = store.review_hands(DB)
            self._send(json.dumps(resp, ensure_ascii=False), "application/json; charset=utf-8")
        elif path == "/api/stats":
            resp = store.stats(DB)
            self._send(json.dumps(resp, ensure_ascii=False), "application/json; charset=utf-8")
        elif path == "/api/leaks":
            resp = store.leak_report(DB)
            self._send(json.dumps(resp, ensure_ascii=False), "application/json; charset=utf-8")
        elif path == "/api/handgrid":
            qs = parse_qs(urlparse(self.path).query)
            pos = qs.get("pos", [""])[0] or None
            stack = qs.get("stack", [""])[0] or None
            resp = store.hand_grid(DB, pos=pos, stack=stack)
            self._send(json.dumps(resp, ensure_ascii=False), "application/json; charset=utf-8")
        elif path == "/api/handsby":
            qs = parse_qs(urlparse(self.path).query)
            combo = qs.get("combo", [""])[0]
            pos = qs.get("pos", [""])[0] or None
            stack = qs.get("stack", [""])[0] or None
            resp = store.hands_by_combo(DB, combo, pos=pos, stack=stack)
            self._send(json.dumps(resp, ensure_ascii=False), "application/json; charset=utf-8")
        elif path == "/api/tournament":
            qs = parse_qs(urlparse(self.path).query)
            tid = qs.get("id", [""])[0]
            resp = store.tournament_hands(DB, tid)
            self._send(json.dumps(resp, ensure_ascii=False), "application/json; charset=utf-8")
        elif path == "/api/bankroll":
            resp = bankroll.summary(DB)
            self._send(json.dumps(resp, ensure_ascii=False), "application/json; charset=utf-8")
        else:
            self.send_error(404)

    def _stream_ai(self, system, user):
        """AI 스트리밍 응답 공통 처리. 성공 시 전체 텍스트, 실패 시 None 반환."""
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-AI-Backend", AI_BACKEND.name)
        self.end_headers()
        full = []
        ok = True
        try:
            for chunk in AI_BACKEND.stream(system, user):
                full.append(chunk)
                self.wfile.write(chunk.encode("utf-8"))
                self.wfile.flush()
        except BrokenPipeError:
            ok = False  # 클라이언트가 연결을 끊음 — 불완전 결과는 저장 안 함
        except Exception as e:
            ok = False
            try:
                self.wfile.write(f"\n\n> ⚠️ 분석 중 오류: {e}".encode("utf-8"))
                self.wfile.flush()
            except BrokenPipeError:
                pass
        text = "".join(full).strip()
        return text if ok and text else None

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
            bank_added = 0
            if added:
                bank_added = bankroll.add_from_hands(DB)   # 새 핸드 토너를 뱅크롤에 자동 추가(상금은 수동 입력)
                persist(DB)
            if not added and not skipped:
                resp = {"error": "핸드를 찾지 못했습니다. 'CoinPoker Hand #' 로 시작하는 로그인지 확인하세요."}
            else:
                resp = {"added": added, "skipped": skipped, "bankroll_added": bank_added}
                resp.update(store.tournament_list(DB))
                resp["report"] = DB.get("report")
                resp["analyzed_total"] = sum(1 for r in DB["hands"].values() if r.get("analysis"))
                resp["review_count"] = sum(1 for r in DB["hands"].values() if r.get("review"))
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
            # 스트리밍 응답 + 완료된 분석은 DB에 영구 저장
            text = self._stream_ai(ANALYSIS_SYSTEM_PROMPT,
                                   "다음 핸드를 분석하세요:\n\n" + hand_md)
            hand_id = body.get("hand_id")
            if text and hand_id in DB["hands"]:
                DB["hands"][hand_id]["analysis"] = text
                persist(DB)
        elif self.path == "/api/report":
            # 분석된 핸드들을 모아 반복 실수 패턴 종합 리포트 생성
            analyzed = [(hid, r) for hid, r in DB["hands"].items() if r.get("analysis")]
            if len(analyzed) < 3:
                self._send(json.dumps(
                    {"error": f"분석된 핸드가 {len(analyzed)}개뿐입니다. "
                              "3개 이상 분석한 뒤 리포트를 생성하세요."},
                    ensure_ascii=False), "application/json; charset=utf-8", code=400)
                return
            # 최신순 최대 100개 (토큰 한도 보호)
            analyzed.sort(key=lambda x: x[1].get("datetime") or "", reverse=True)
            analyzed = analyzed[:100]
            blocks = []
            for hid, r in analyzed:
                cards = " ".join(r.get("hero_cards") or [])
                net_bb = r.get("net_bb")
                net_s = f"{net_bb:+}bb" if net_bb is not None else "?"
                blocks.append(
                    f"[핸드 #{hid} | {r.get('datetime', '?')} | {r.get('hero_pos', '?')} "
                    f"| {cards} | net {net_s}]\n{r['analysis']}"
                )
            user = (f"다음은 Hero의 핸드 {len(analyzed)}개에 대한 분석 모음입니다. "
                    f"종합 리포트를 작성하세요.\n\n" + "\n\n---\n\n".join(blocks))
            text = self._stream_ai(REPORT_SYSTEM_PROMPT, user)
            if text:
                DB["report"] = {
                    "text": text,
                    "created_at": time.strftime("%Y-%m-%d %H:%M"),
                    "hand_count": len(analyzed),
                }
                persist(DB)
        elif self.path in ("/api/bankroll/entry", "/api/bankroll/delete", "/api/bankroll/confirm"):
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except ValueError:
                self._send(json.dumps({"error": "잘못된 요청"}, ensure_ascii=False),
                           "application/json; charset=utf-8", code=400)
                return
            if self.path == "/api/bankroll/delete":
                bankroll.delete_entry(DB, body.get("id"))
            elif self.path == "/api/bankroll/confirm":
                bankroll.confirm_entry(DB, body.get("id"))
            elif body.get("id"):
                bankroll.update_entry(DB, body["id"], body)
            else:
                bankroll.add_entry(DB, body)
            persist(DB)
            self._send(json.dumps(bankroll.summary(DB), ensure_ascii=False),
                       "application/json; charset=utf-8")
        elif self.path == "/api/bankroll/balance":
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                amount = float(body["amount"])
            except (ValueError, KeyError, TypeError):
                self._send(json.dumps({"error": "잘못된 요청"}, ensure_ascii=False),
                           "application/json; charset=utf-8", code=400)
                return
            # 날짜 미지정 = '지금' 스냅샷 → 현재 시각까지 기록(같은 날 이후 토너도 자동 합산).
            # 날짜 지정(소급 입력)이면 at=None → set_balance가 그날 끝으로 처리.
            date = body.get("date")
            at = None
            if not date:
                date = time.strftime("%Y-%m-%d")
                at = time.strftime("%Y-%m-%d %H:%M:%S")
            bankroll.set_balance(DB, date, amount, at=at)
            persist(DB)
            self._send(json.dumps(bankroll.summary(DB), ensure_ascii=False),
                       "application/json; charset=utf-8")
        elif self.path in ("/api/bankroll/cashflow", "/api/bankroll/cashflow/delete"):
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except ValueError:
                self._send(json.dumps({"error": "잘못된 요청"}, ensure_ascii=False),
                           "application/json; charset=utf-8", code=400)
                return
            if self.path.endswith("/delete"):
                bankroll.delete_cashflow(DB, body.get("id"))
            else:
                try:
                    float(body.get("amount"))
                except (ValueError, TypeError):
                    self._send(json.dumps({"error": "금액을 입력하세요"}, ensure_ascii=False),
                               "application/json; charset=utf-8", code=400)
                    return
                bankroll.add_cashflow(DB, body)
            persist(DB)
            self._send(json.dumps(bankroll.summary(DB), ensure_ascii=False),
                       "application/json; charset=utf-8")
        else:
            self.send_error(404)

    def log_message(self, *args):  # 콘솔 로그 끄기
        pass


def main():
    global DB, DB_PATH, AI_BACKEND, CLOUD, _last_pushed_hash
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
    CLOUD = cloud_sync.available()
    if CLOUD:
        # 클라우드 모드: 로컬 작업폴더는 깨끗하게 두고 ~/.cache에 캐시(안전망)만 둔다.
        cache_dir = os.path.expanduser("~/.cache/analyze_hand_history")
        os.makedirs(cache_dir, exist_ok=True)
        DB_PATH = os.path.join(cache_dir, "hands_db.json")
        print(f"☁️  클라우드 동기화 ON — {cloud_sync.repo()} / Release:{cloud_sync.tag()}")
        try:
            remote = cloud_sync.pull()
            if remote is not None:
                DB = remote
                store.save_db(DB_PATH, DB)                  # 로컬 캐시 갱신
                print(f"   클라우드에서 받음 — 핸드 {len(DB['hands'])}개")
            else:
                DB = store.load_db(DB_PATH)
                print("   클라우드에 DB 없음 — 첫 저장 시 업로드됩니다")
        except cloud_sync.CloudError as e:
            DB = store.load_db(DB_PATH)                      # 받기 실패 → 캐시로 시작
            print(f"⚠️  클라우드 받기 실패 — 로컬 캐시로 시작: {e}")
        _last_pushed_hash = _db_hash(DB)                    # 받은 직후 = 이미 업로드된 상태
    else:
        hint = cloud_sync.config_hint()
        if hint:
            print(f"ℹ️  부분 설정 감지 — {hint}")
        DB = store.load_db(DB_PATH)

    if args.rebuild and DB["hands"]:
        store.rebuild(DB, hero=args.hero)
        persist(DB)
        print(f"재변환 완료: {len(DB['hands'])}개 핸드")

    # CLI로 받은 파일은 시작 시 DB에 임포트
    for path in args.input:
        with open(path, encoding="utf-8") as f:
            added, skipped = store.import_text(DB, f.read(), hero=args.hero)
        print(f"{path}: 신규 {added}개 추가, 기존 {skipped}개 스킵")
        if added:
            persist(DB)

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
    finally:
        if CLOUD and _db_dirty:                 # 미반영 변경이 있을 때만 업로드
            print("☁️  마지막 변경 동기화 중... (끄지 마세요)")
            _flush_push()
            print("✅ 동기화 완료")


if __name__ == "__main__":
    main()
