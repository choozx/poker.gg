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
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import bankroll
import cloud_sync
import coach
import convert
import quiz
import ranges
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
- 포스트플랍 액션 순서: OOP(블라인드 쪽)가 먼저, IP(버튼 쪽)가 나중에 행동합니다. Hero가 OOP면 그 스트리트에서 상대의 액션을 보기 전에 결정해야 하므로, "상대가 체크해서 약하니 벳" 같은 근거는 Hero가 IP일 때만 유효합니다.
- 핸드에 적힌 줄 순서가 실제 행동 순서입니다. 어떤 결정을 평가할 때, 그 줄보다 아래에 있는 상대 액션은 당시 Hero가 알 수 없었던 정보이니 근거로 쓰지 마세요.
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

COACH_SYSTEM_PROMPT = """\
당신은 NLH 토너먼트 전문 포커 코치입니다. 한 플레이어(Hero, 질문하는 사람)가 자기 플레이에 대해
대화를 나눕니다. 함께 주어지는 것:
- "내 플레이 요약": 앱이 그 사람의 핸드 DB에서 계산한 **실제 숫자** (VPIP/PFR, 포지션별 칩 EV,
  차트 대비 오픈율·방어율, 약점 스팟, AI 분석 등급, 연습 성적).
- "참조 핸드": 그 사람이 #번호로 짚은 핸드의 원문 (있을 때만).
- "지난 대화": 이 대화의 앞부분.

규칙:
- 숫자는 요약에 있는 값만 인용하세요. 요약에 없는 통계를 지어내지 말고, 없으면 없다고 말하세요.
- 특정 핸드 이야기는 참조 핸드 원문으로만 하세요. 원문이 없는 핸드는 추측하지 말고
  "#핸드번호로 짚어 달라"고 요청하세요.
- 표본이 작으면(기회 수십 회 이하) 그렇다고 밝히고 단정하지 마세요.
- 차트는 가져온 GTO 차트 또는 내장 근사이고, 약점 스팟의 기준선은 대략적인 참고값입니다.
- 토너먼트 칩은 상금이 아닙니다. 칩 EV를 돈으로 환산하지 마세요. 결과론 금지.
- 한국어, 마크다운, 간결하게. 질문에 먼저 직접 답하고, 필요하면 구체적인 교정 1~2개를 제시하세요.
"""


QUIZ_GRADE_SYSTEM_PROMPT = """\
당신은 NLH 토너먼트 전문 포커 코치입니다. 학생에게 낸 스팟 문제의 답을 채점하세요.

주어지는 것: 히어로의 결정 직전까지의 핸드 상황과, 학생이 고른 액션 하나.
핸드는 결정 지점에서 잘려 있습니다 — 그 뒤에 무슨 일이 일어났는지는 당신도 모르고,
알 필요도 없습니다. 결정 시점에 알 수 있던 정보만으로 판단하세요.

형식 (정확히 준수):
첫 줄: "판정: [좋음]" — [좋음/무난/의문/실수] 중 하나. 반드시 대괄호 포함.
둘째 줄부터:
- **왜** — 학생의 선택을 포지션·스택 깊이(bb)·팟 오즈·상대 예상 레인지로 2~3문장 평가.
- **최선** — 이 스팟의 가장 좋은 액션과 그 이유 1~2문장. 학생의 선택이 최선이면 그렇다고 쓰세요.
- **기억할 것** — 다음에 같은 스팟에서 쓸 판단 기준 한 줄.

규칙:
- 결과론 금지. 어떤 카드가 나왔을지 추측해서 평가하지 마세요.
- 학생이 고른 액션이 실제로 히어로가 친 액션인지 아닌지는 알 수 없습니다. 추측하지 마세요.
- 포스트플랍은 OOP(블라인드 쪽)가 먼저, IP(버튼 쪽)가 나중에 행동합니다.
- 토너먼트이므로 스택 보존과 ICM 관점도 고려하세요.
- 한국어, 마크다운, 간결하게. 상황 재서술 금지.
"""

QUIZ_GEN_SYSTEM_PROMPT = """\
당신은 NLH 토너먼트 전문 포커 코치입니다. 학생의 약점 스팟에 맞는 연습 문제를 하나 만드세요.

출력은 **JSON 객체 하나만**. 코드펜스·설명·인사말 없이 JSON만 출력하세요.
{
  "situation": "마크다운 문자열",
  "choices": [{"id": "a", "label": "폴드"}, {"id": "b", "label": "콜 — 4,500 (3.0bb)"}, ...]
}

situation 마크다운은 아래 형식을 그대로 따르세요 (실제 핸드 히스토리와 같은 모양):
## 연습 문제
NLH | Blinds 300/600 ante 75 | 7-handed

**Players:**
- UTG player1: 24,000 (40.0bb)
- ... (전 좌석. 히어로 줄 끝에 ` ← **HERO**`)

**Hero hole cards: [Ah Kd]** (CO)

**PREFLOP** (pot: 1,425 = 2.4bb)
- UTG player1 raises to 1,500 (2.5bb)
- ...
- CO Hero → **???  당신의 차례입니다** (to call 1,500, pot 2,925, pot odds 34%)

규칙:
- 요청받은 포지션·스택 깊이·상황 유형을 정확히 반영하세요.
- 히어로의 결정 지점에서 끊고, 그 뒤(히어로의 액션·이후 액션·다음 스트리트·결과)는 절대 쓰지 마세요.
- 상대 홀카드는 쓰지 마세요.
- choices는 2~4개, 그 시점에 실제로 가능한 액션만, 금액을 칩과 bb로 함께 표기하세요.
- 정답이 뻔하지 않은, 실제로 고민되는 스팟으로 만드세요.
- 칩·팟·bb 계산이 서로 맞아떨어지게 하세요.
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

_QUIZ_GRADE_RE = re.compile(r"\[(좋음|무난|의문|실수)\]")


def _quiz_grade_of(text):
    """채점 텍스트에서 첫 [등급] 을 뽑는다. 프론트 배지와 성적표가 이 값을 쓴다."""
    m = _QUIZ_GRADE_RE.search(text or "")
    return m.group(1) if m else None


def _quiz_filters(qs):
    """?pos=BB,SB&stack=pf,deep&street=turn,river → (포지션, 스택, 스트릿). 빈 값이면 전체."""
    def split(key):
        return [v for v in (qs.get(key, [""])[0] or "").split(",") if v]
    return split("pos"), split("stack"), split("street")


def _quiz_parse_gen(text):
    """AI가 생성한 문제 JSON 파싱. 코드펜스/앞뒤 잡담을 관대하게 걷어낸다."""
    s = (text or "").strip()
    if "```" in s:                                   # ```json ... ``` 펜스 제거
        parts = s.split("```")
        s = max(parts, key=len)
        if s.lstrip().startswith("json"):
            s = s.lstrip()[4:]
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        obj = json.loads(s[i:j + 1])
    except ValueError:
        return None
    situation = (obj.get("situation") or "").strip()
    choices = obj.get("choices")
    if not situation or not isinstance(choices, list) or len(choices) < 2:
        return None
    clean = []
    for k, c in enumerate(choices[:4]):
        if isinstance(c, dict) and c.get("label"):
            clean.append({"id": str(c.get("id") or k), "label": str(c["label"])})
    if len(clean) < 2:
        return None
    return {"situation": situation, "choices": clean}


DB = None        # main()에서 로드되는 핸드 DB
DB_PATH = None   # 로컬 저장 경로 (클라우드 모드면 ~/.cache 캐시, 아니면 --db)
HERO = "Hero"    # --hero 로 지정하는 히어로 플레이어 이름 (main()에서 설정)

# --- 클라우드 동기화 (opt-in) ------------------------------------------------
# 저장(persist)될 때마다 변경을 표시하고, 잠잠해지면(디바운스) 딱 한 번 업로드한다.
# 내용이 직전 업로드와 같으면 스킵 → 과금·트래픽·API 호출 최소.
CLOUD = False                      # main()에서 cloud_sync.available()로 결정
DEBOUNCE_SEC = 8.0
_push_lock = threading.Lock()
_push_timer = None
_last_pushed_hash = None
_db_dirty = False


def _db_snapshot():
    """업로드할 DB 바이트 — **살아있는 DB dict가 아니라 디스크 파일에서** 읽는다.

    푸시는 별도 스레드에서 도는데, 그 사이 요청 스레드가 DB를 조금이라도 건드리면
    json.dumps가 'dictionary changed size during iteration'으로 터진다.
    persist()가 이미 store.save_db로 원자적으로(.tmp → os.replace, 락 안에서) 써 둔
    파일을 그대로 올리면 항상 일관된 스냅샷이 보장된다 (직렬화 형식도 indent=1로 동일)."""
    with open(DB_PATH, "rb") as f:
        return f.read()


def _do_push():
    """디바운스 만료/종료 시 실제 업로드. 내용이 직전과 같으면 스킵."""
    global _last_pushed_hash, _db_dirty
    with _push_lock:
        if not _db_dirty:
            return
        try:
            raw = _db_snapshot()
        except OSError as e:
            print(f"⚠️  DB 스냅샷 읽기 실패 — 업로드 건너뜀: {e}")
            return
        h = hashlib.sha256(raw).hexdigest()
        if h == _last_pushed_hash:        # 저장은 일어났지만 내용 동일(예: --rebuild)
            _db_dirty = False
            return
        try:
            n = cloud_sync.push_raw(raw)
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


# 화면(HTML·CSS·JS)은 web/ 아래 파일이다 (예전엔 이 자리에 4,000줄짜리 INDEX_HTML 문자열이 있었다).
# 요청마다 파일을 읽으므로 화면 코드를 고친 뒤 서버 재시작 없이 새로고침만 하면 반영된다.
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
# /static/ 아래로 내보내는 파일 — 목록에 있는 것만 (경로 조작으로 다른 파일을 읽지 못하게)
WEB_STATIC = {"app.css": "text/css; charset=utf-8",
              "app.js": "application/javascript; charset=utf-8"}


def web_file(name):
    with open(os.path.join(WEB_DIR, name), "rb") as f:
        return f.read()



class Handler(BaseHTTPRequestHandler):
    def _send(self, body, ctype="text/html; charset=utf-8", code=200, headers=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        from urllib.parse import parse_qs, urlparse
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            # no-cache: 화면 파일을 고친 뒤 새로고침하면 바로 새 버전을 받게
            self._send(web_file("index.html"), headers={"Cache-Control": "no-cache"})
        elif path.startswith("/static/") and path[len("/static/"):] in WEB_STATIC:
            name = path[len("/static/"):]
            self._send(web_file(name), WEB_STATIC[name], headers={"Cache-Control": "no-cache"})
        elif path == "/api/db":
            resp = store.tournament_list(DB)
            resp["report"] = DB.get("report")
            resp["analyzed_total"] = sum(1 for r in DB["hands"].values() if r.get("analysis"))
            resp["review_count"] = sum(1 for r in DB["hands"].values() if r.get("review"))
            self._send(json.dumps(resp, ensure_ascii=False), "application/json; charset=utf-8")
        elif path == "/api/review":
            resp = store.review_hands(DB)
            resp["chart_devs"] = ranges.annotate_deviations(DB, resp["hands"])
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
            resp = store.hands_by_combo(DB, combo, pos=pos, stack=stack, hero=HERO)
            resp["chart_devs"] = ranges.annotate_deviations(DB, resp["hands"])
            self._send(json.dumps(resp, ensure_ascii=False), "application/json; charset=utf-8")
        elif path == "/api/tournament":
            qs = parse_qs(urlparse(self.path).query)
            tid = qs.get("id", [""])[0]
            resp = store.tournament_hands(DB, tid)
            # 핸드마다 프리플랍이 가져온 차트와 어긋났는지 (chart_dev) — 복기할 핸드를 바로 고르게
            resp["chart_devs"] = ranges.annotate_deviations(DB, resp["hands"])
            self._send(json.dumps(resp, ensure_ascii=False), "application/json; charset=utf-8")
        elif path == "/api/bankroll":
            resp = bankroll.summary(DB)
            self._send(json.dumps(resp, ensure_ascii=False), "application/json; charset=utf-8")
        elif path == "/api/quiz/spots":
            qs = parse_qs(urlparse(self.path).query)
            pos, stacks, streets = _quiz_filters(qs)
            self._send(json.dumps(
                quiz.spots_view(DB, positions=pos, stacks=stacks, streets=streets),
                ensure_ascii=False), "application/json; charset=utf-8")
        elif path == "/api/quiz/next":
            qs = parse_qs(urlparse(self.path).query)
            pos, stacks, streets = _quiz_filters(qs)
            resp = quiz.next_question(DB, spot_key=qs.get("spot", [""])[0] or None,
                                      hero=HERO, positions=pos, stacks=stacks,
                                      streets=streets)
            self._send(json.dumps(resp, ensure_ascii=False), "application/json; charset=utf-8")
        elif path == "/api/coach/chats":
            self._send(json.dumps(coach.chat_list(DB), ensure_ascii=False),
                       "application/json; charset=utf-8")
        elif path == "/api/coach/chat":
            qs = parse_qs(urlparse(self.path).query)
            chat = coach.get_chat(DB, qs.get("id", [""])[0])
            self._send(json.dumps(chat or {"error": "대화를 찾지 못했습니다."}, ensure_ascii=False),
                       "application/json; charset=utf-8", code=200 if chat else 404)
        elif path == "/api/range/leaks":
            qs = parse_qs(urlparse(self.path).query)
            resp = ranges.leak_report(DB, max_seats=qs.get("max", ["8"])[0] or 8)
            self._send(json.dumps(resp, ensure_ascii=False), "application/json; charset=utf-8")
        elif path == "/api/range/hands":
            qs = parse_qs(urlparse(self.path).query)
            g = lambda k: qs.get(k, [""])[0]
            resp = ranges.spot_hands(DB, g("pos"), g("vs") or None, g("bucket"), g("combo"), hero=HERO)
            resp["chart_devs"] = ranges.annotate_deviations(DB, resp["hands"])
            self._send(json.dumps(resp, ensure_ascii=False), "application/json; charset=utf-8")
        elif path == "/api/range/state":
            self._send(json.dumps(ranges.state_view(DB), ensure_ascii=False),
                       "application/json; charset=utf-8")
        elif path == "/api/range/next":
            qs = parse_qs(urlparse(self.path).query)
            pos, stacks, _ = _quiz_filters(qs)
            # spot=open(오픈 차트만) / spot=CO·CO-allin(그 방어 차트만) — 리크 리포트의 '이 스팟 드릴'.
            # 빈 vs=로 보내면 parse_qs가 키째 버리므로 오픈은 'open'이라는 이름으로 받는다
            spot = qs.get("spot", [""])[0]
            resp = ranges.next_question(DB, positions=pos, stacks=stacks,
                                        max_seats=qs.get("max", ["8"])[0] or 8,
                                        vs_only=None if not spot else ("" if spot == "open" else spot))
            self._send(json.dumps(resp, ensure_ascii=False), "application/json; charset=utf-8")
        elif path == "/api/range/chart":
            qs = parse_qs(urlparse(self.path).query)
            resp = ranges.chart_view(DB, qs.get("pos", [""])[0],
                                     qs.get("stack", [""])[0],
                                     vs=qs.get("vs", [""])[0] or None)
            self._send(json.dumps(resp, ensure_ascii=False),
                       "application/json; charset=utf-8",
                       code=400 if resp.get("error") else 200)
        elif path == "/api/quiz/reveal":
            qs = parse_qs(urlparse(self.path).query)
            resp = quiz.reveal(DB, qs.get("hand_id", [""])[0],
                               int(qs.get("didx", ["0"])[0] or 0), hero=HERO)
            self._send(json.dumps(resp or {"error": "핸드를 찾지 못했습니다."}, ensure_ascii=False),
                       "application/json; charset=utf-8")
        else:
            self.send_error(404)

    def _stream_ai(self, system, user, headers=None):
        """AI 스트리밍 응답 공통 처리. 성공 시 전체 텍스트, 실패 시 None 반환.
        `headers`는 본문보다 먼저 보내야 하는 메타 (코치의 대화 id 등)."""
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-AI-Backend", AI_BACKEND.name)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
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
                # markdown은 저장하지 않으므로 hand_id로 raw에서 즉석 렌더. (구 클라이언트가
                # 보낸 body markdown은 폴백.) 복기/그리드 분석은 hand_id만으로 동작.
                hand_id = body.get("hand_id")
                rec = DB["hands"].get(hand_id)
                if rec and rec.get("raw"):
                    hand_md = convert.render_markdown(
                        convert.parse_hand(rec["raw"]), hero="Hero")
                else:
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
        elif self.path == "/api/quiz/grade":
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                situation = (body.get("situation") or "").strip()
                choice = (body.get("choice_label") or "").strip()
                if not situation or not choice:
                    raise ValueError("문제 또는 선택한 액션이 비어 있습니다.")
                if AI_BACKEND is None:
                    raise RuntimeError(
                        "사용 가능한 AI 백엔드가 없습니다. claude CLI 설치 또는 "
                        "ANTHROPIC_API_KEY 설정 후 다시 실행하세요.")
            except Exception as e:
                self._send(json.dumps({"error": str(e)}, ensure_ascii=False),
                           "application/json; charset=utf-8", code=400)
                return
            hand_id, didx = body.get("hand_id"), body.get("didx")
            choice_id = body.get("choice_id") or "?"
            # 같은 핸드·같은 결정 지점·같은 선택은 이미 채점한 적이 있으면 재사용 (AI 호출 0회)
            hit = (quiz.cache_get(DB, hand_id, didx, choice_id)
                   if hand_id is not None and didx is not None else None)
            if hit:
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("X-AI-Backend", "cache")
                self.end_headers()
                self.wfile.write(hit["text"].encode("utf-8"))
                text, grade = hit["text"], hit["grade"]
            else:
                text = self._stream_ai(
                    QUIZ_GRADE_SYSTEM_PROMPT,
                    f"[문제 상황]\n{situation}\n\n[학생이 고른 액션]\n{choice}\n\n채점하세요.")
                grade = _quiz_grade_of(text) if text else None
            if text:
                if hand_id is not None and didx is not None and not hit:
                    quiz.cache_put(DB, hand_id, didx, choice_id, grade, text)
                quiz.record_attempt(DB, body.get("spot"), hand_id, body.get("street"),
                                    choice_id, grade, generated=body.get("source") == "ai")
                persist(DB)
        elif self.path == "/api/coach/send":
            # 💬 AI 코치 — 내 플레이 요약 + 짚은 핸드 + 최근 대화를 실어 한 번 호출 (스트리밍).
            # 답이 끝까지 왔을 때만 질문·답을 함께 저장한다 (끊긴 답은 남기지 않는다)
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                text = (body.get("text") or "").strip()
                if not text:
                    raise ValueError("질문을 입력하세요.")
                if AI_BACKEND is None:
                    raise RuntimeError(
                        "사용 가능한 AI 백엔드가 없습니다. claude CLI 설치 또는 "
                        "ANTHROPIC_API_KEY 설정 후 다시 실행하세요.")
                chat_id = body.get("chat_id") or coach.new_id()
                prompt, refs, missing = coach.build_prompt(DB, chat_id, text, hero=HERO)
            except Exception as e:
                self._send(json.dumps({"error": str(e)}, ensure_ascii=False),
                           "application/json; charset=utf-8", code=400)
                return
            reply = self._stream_ai(COACH_SYSTEM_PROMPT, prompt, headers={
                "X-Chat-Id": chat_id,
                "X-Coach-Refs": ",".join(refs),
                "X-Coach-Missing": ",".join(missing),
            })
            if reply:
                coach.save_exchange(DB, chat_id, text, reply, refs)
                persist(DB)
        elif self.path == "/api/coach/delete":
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except ValueError:
                self._send(json.dumps({"error": "잘못된 요청"}, ensure_ascii=False),
                           "application/json; charset=utf-8", code=400)
                return
            resp = coach.delete_chat(DB, body.get("id"))
            if resp["ok"]:
                persist(DB)
            self._send(json.dumps(resp, ensure_ascii=False), "application/json; charset=utf-8")
        elif self.path == "/api/range/grade":
            # 오픈 레인지 채점은 **전부 로컬** — 정답이 차트에 있으므로 AI를 부르지 않는다
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except ValueError:
                self._send(json.dumps({"error": "잘못된 요청"}, ensure_ascii=False),
                           "application/json; charset=utf-8", code=400)
                return
            resp = ranges.grade(DB, body.get("pos"), body.get("stack"),
                                body.get("combo"), body.get("choice"),
                                vs=body.get("vs") or None)
            if not resp.get("error"):
                resp["scoreboard"] = ranges.scoreboard(DB)
                persist(DB)
            self._send(json.dumps(resp, ensure_ascii=False),
                       "application/json; charset=utf-8",
                       code=400 if resp.get("error") else 200)
        elif self.path == "/api/range/import":
            # GTO 툴에서 복사한 레인지 텍스트를 (포지션, 스택[, 상대]) 슬롯에 저장 — 내장 차트를 덮어쓴다
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except ValueError:
                self._send(json.dumps({"error": "잘못된 요청"}, ensure_ascii=False),
                           "application/json; charset=utf-8", code=400)
                return
            resp = ranges.import_chart(DB, body.get("pos"), body.get("stack"),
                                       body.get("text"), source=body.get("source"),
                                       jam=body.get("jam"), call=body.get("call"),
                                       vs=body.get("vs") or None)
            if resp.get("ok"):
                persist(DB)
            self._send(json.dumps(resp, ensure_ascii=False),
                       "application/json; charset=utf-8",
                       code=400 if resp.get("error") else 200)
        elif self.path == "/api/range/delete-chart":
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except ValueError:
                self._send(json.dumps({"error": "잘못된 요청"}, ensure_ascii=False),
                           "application/json; charset=utf-8", code=400)
                return
            resp = ranges.delete_chart(DB, body.get("pos"), body.get("stack"),
                                       vs=body.get("vs") or None)
            persist(DB)
            self._send(json.dumps(resp, ensure_ascii=False), "application/json; charset=utf-8")
        elif self.path == "/api/quiz/gen":
            # 실제 핸드가 소진된 스팟 — AI가 같은 성격의 연습 문제를 새로 만든다
            length = int(self.headers.get("Content-Length", 0))
            try:
                spot = json.loads(self.rfile.read(length).decode("utf-8"))
                if AI_BACKEND is None:
                    raise RuntimeError(
                        "사용 가능한 AI 백엔드가 없습니다. claude CLI 설치 또는 "
                        "ANTHROPIC_API_KEY 설정 후 다시 실행하세요.")
            except Exception as e:
                self._send(json.dumps({"error": str(e)}, ensure_ascii=False),
                           "application/json; charset=utf-8", code=400)
                return
            street = spot.get("street")
            user = (f"학생의 약점 스팟: {spot.get('label')}\n"
                    f"근거: {spot.get('detail')}\n"
                    f"포지션: {spot.get('pos')} · 스택 깊이: {spot.get('stack')} · "
                    f"상황 유형: {spot.get('reason')}\n"
                    + (f"결정 스트릿: {street} — 반드시 이 스트릿에서 히어로가 결정하는 "
                       f"지점으로 끊으세요.\n" if street else "")
                    + "\n이 스팟의 연습 문제를 JSON으로 하나 만드세요.")
            raw = []
            try:
                for chunk in AI_BACKEND.stream(QUIZ_GEN_SYSTEM_PROMPT, user):
                    raw.append(chunk)
            except Exception as e:
                self._send(json.dumps({"error": f"문제 생성 실패: {e}"}, ensure_ascii=False),
                           "application/json; charset=utf-8", code=500)
                return
            q = _quiz_parse_gen("".join(raw))
            if not q:
                self._send(json.dumps(
                    {"error": "AI가 만든 문제를 해석하지 못했습니다. 다시 시도해 주세요."},
                    ensure_ascii=False), "application/json; charset=utf-8", code=502)
                return
            q.update({"source": "ai", "spot": spot.get("key"),
                      "spot_label": spot.get("label"),
                      "street": spot.get("street") or "AI 생성"})
            self._send(json.dumps({"question": q}, ensure_ascii=False),
                       "application/json; charset=utf-8")
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
                # 기록 시각: 오늘 날짜(현재 거래)면 지금 시각 → 마지막 잔고 입력 이후로 잡혀
                # 잔고에 즉시 반영. 과거 날짜(소급)면 그날 끝 → 앵커 이전이면 이미 반영된 걸로 처리.
                today = time.strftime("%Y-%m-%d")
                cf_date = (body.get("date") or "").strip() or today
                body["date"] = cf_date
                body["at"] = (time.strftime("%Y-%m-%d %H:%M:%S")
                              if cf_date >= today else cf_date + " 23:59:59")
                bankroll.add_cashflow(DB, body)
            persist(DB)
            self._send(json.dumps(bankroll.summary(DB), ensure_ascii=False),
                       "application/json; charset=utf-8")
        else:
            self.send_error(404)

    def log_message(self, *args):  # 콘솔 로그 끄기
        pass


def main():
    global DB, DB_PATH, AI_BACKEND, CLOUD, HERO, _last_pushed_hash
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
    HERO = args.hero
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
        # 받은 직후 = 이미 업로드된 상태. _do_push와 같은 기준(파일 바이트)으로 재야
        # 첫 저장 때 불필요한 업로드가 한 번 더 나가지 않는다.
        try:
            _last_pushed_hash = hashlib.sha256(_db_snapshot()).hexdigest()
        except OSError:
            _last_pushed_hash = None
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
