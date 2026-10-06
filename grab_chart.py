#!/usr/bin/env python3
"""GTO 툴 화면의 13×13 레인지 그리드를 캡처해서 차트 슬롯에 넣는다.

GTO 위자드 무료 플랜처럼 레인지를 **텍스트로 복사할 수 없을 때** 쓴다. 화면을 찍어
격자를 자동으로 찾고, 셀마다 색이 가로로 차지한 비율을 세서 빈도를 **계측**한다
(눈대중 추정이 아니다). 결과는 실행 중인 앱의 `POST /api/range/import`로 보내므로
저장·클라우드 동기화는 앱이 평소대로 처리한다.

사용법:
    python3 grab_chart.py UTG 20                    # 캡처 → 파싱 → 임포트
    python3 grab_chart.py UTG 20 --delay 3          # 3초 뒤 캡처 (브라우저로 전환할 시간)
    python3 grab_chart.py LJ 30 --image ~/a.png     # 캡처 대신 이미지 파일에서
    python3 grab_chart.py CO 50 --dry-run           # 읽기만 하고 보내지 않음
    python3 grab_chart.py BB 20 --vs BTN            # 방어 차트: BTN 오픈을 받은 BB
    python3 grab_chart.py BB 13 --vs UTG --allin    # 방어 차트: UTG 오픈 올인을 받은 BB
    python3 grab_chart.py --watch                   # 감시 모드: 없는 차트를 순서대로 연달아 (아래 참고)
    python3 grab_chart.py --watch --stacks 30 --only BB

포지션: UTG UTG1 LJ HJ CO BTN SB BB (8맥스, `ranges.POS_8MAX`. 소문자·UTG+1도 받는다)
스택:   GTO 툴에 적힌 bb 숫자를 그대로 준다 (13, 20, 100 …). 버킷 키(pf/short/mid/deep)도 받는다.
상대:   `--vs`를 주면 그 오프너의 오픈을 받았을 때의 **방어 차트**다 (BB는 필수 — 오픈 기회가 없다).
        색 판정은 같고 뜻만 바뀐다: 빨강=3벳, 진한 빨강=올인, 초록=콜.
        오프너가 **올인**으로 열었으면 `--allin`을 더한다 (`--vs UTG --allin`). 남은 액션이 콜/폴드뿐인
        별개 상황이라 따로 저장되고, 폴드가 아닌 칸은 색과 상관없이 전부 콜로 저장된다.

**bb 숫자는 그대로 슬롯이 된다** — 10bb와 13bb 차트가 따로 산다. 드릴 채점은 여전히 4버킷
단위라, 한 버킷에 여러 장이 있으면 그 구간에서 실제로 가장 흔한 스택에 가까운 차트가 쓰인다
(`ranges._BUCKET_MID`). 같은 bb에 다시 넣으면 덮어쓰고, 그때는 기존 차트 정보를 먼저 알려준다.

표준 라이브러리만 쓴다. 화면 캡처는 맥 기본 `screencapture`, 이미지 디코드는 BMP를
직접 읽는다(PNG 등은 맥 기본 `sips`로 BMP 변환). 색 판정은 팔레트를 고정하지 않는다:
**파랑 계열=폴드 / 빨강 계열=액션**으로 가른 뒤, 빨강의 **진하기**를 2-평균으로 다시 갈라
진한 쪽을 올인, 밝은 쪽을 레이즈로 본다(`split_reds`). 올인 빈도는 `jam`으로 따로 보내
차트에 두 톤 그대로 그려진다 — 다만 **채점은 합계 기준**이다 (오픈이냐 폴드냐만 묻는다).
**초록=콜(림프)**은 따로 센다: `call`로 보내지고, 콜이 있는 차트는 드릴이 오픈/콜/폴드
3지선다가 된다(채점도 세 몫으로).
"""
import argparse
import json
import os
import select
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

RANKS = "AKQJT98765432"
MIN_TONE_GAP = 30          # 빨강 두 톤으로 보려면 밝기가 이만큼은 떨어져 있어야 한다
CALL = "call"              # read_grid에서 '콜 열' 표시 (빨강 열은 픽셀 튜플, 폴드는 None)
BUCKETS = ["pf", "short", "mid", "deep"]
BUCKET_LABEL = {"pf": "<15bb", "short": "15–25bb", "mid": "25–40bb", "deep": "40bb+"}


def parse_pos(arg):
    """포지션 인자 → 8맥스 이름 (없는 포지션이면 None). 목록은 앱의 `ranges.POS_8MAX` 하나뿐."""
    import ranges
    pos = ranges._norm_pos(arg)
    return pos if pos in ranges.POS_8MAX else None


def parse_stack(arg):
    """'13' 같은 bb 숫자나 버킷 키 → (슬롯키, bb, 버킷).

    구간 기준이 두 벌 생기지 않게 **앱의 `ranges.parse_stack`을 그대로** 쓴다."""
    import ranges
    return ranges.parse_stack(arg)


class GridNotFound(SystemExit):
    """화면에 그리드가 없다. 한 장 모드에선 그대로 종료 메시지가 되고, 감시 모드는 잡아서
    '아직 차트가 안 떴다'로 보고 다음 폴링을 기다린다."""


# ── 이미지 읽기 ────────────────────────────────────────────────────────────────

class Img:
    """BMP 한 장. px(x, y) → (r, g, b)."""

    def __init__(self, data):
        if data[:2] != b"BM":
            raise ValueError("BMP가 아닙니다")
        offset = struct.unpack_from("<I", data, 10)[0]
        hdr = struct.unpack_from("<I", data, 14)[0]
        w, h = struct.unpack_from("<ii", data, 18)
        bits = struct.unpack_from("<H", data, 28)[0]
        if bits not in (24, 32):
            raise ValueError(f"지원하지 않는 BMP 색 깊이: {bits}bit")
        self.w, self.h = w, abs(h)
        self.bpp = bits // 8
        self.stride = ((w * self.bpp + 3) // 4) * 4
        self.topdown = h < 0              # 음수 높이 = 위에서 아래로 저장
        self.data = data
        self.offset = offset
        del hdr

    def px(self, x, y):
        row = y if self.topdown else self.h - 1 - y
        i = self.offset + row * self.stride + x * self.bpp
        d = self.data
        return d[i + 2], d[i + 1], d[i]          # BMP는 BGR 순서


def load_image(path):
    """어떤 형식이든 BMP로 바꿔서 읽는다 (맥 기본 sips 사용)."""
    with tempfile.NamedTemporaryFile(suffix=".bmp", delete=False) as f:
        tmp = f.name
    try:
        r = subprocess.run(["sips", "-s", "format", "bmp", path, "--out", tmp],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"이미지 변환 실패: {r.stderr.strip()}")
        with open(tmp, "rb") as f:
            return Img(f.read())
    finally:
        os.unlink(tmp)


def capture(region=None, delay=0.0):
    """화면을 찍어 Img로 돌려준다."""
    if delay:
        print(f"{delay:g}초 뒤 캡처합니다 — 차트 화면을 띄워 두세요...")
        time.sleep(delay)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        shot = f.name
    try:
        cmd = ["screencapture", "-x"]            # -x: 셔터음 끄기
        if region:
            cmd += ["-R", region]
        cmd.append(shot)
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"화면 캡처 실패: {r.stderr.strip()}\n"
                             "터미널에 '화면 기록' 권한이 필요할 수 있습니다 "
                             "(시스템 설정 → 개인정보 보호 및 보안 → 화면 기록).")
        return load_image(shot)
    finally:
        os.unlink(shot)


# ── 색 판정 ────────────────────────────────────────────────────────────────────

def kind(p):
    """픽셀 한 점 → 'fold'(파랑) / 'act'(빨강) / 'call'(초록) / 'line'(어두움) / None(글자·그 외).

    팔레트를 고정하지 않는다. 툴 테마가 바뀌어 빨강·파랑의 정확한 값이 달라져도 동작한다.
    빨강 두 톤(레이즈/올인)은 여기서 나누지 않고 `split_reds`가 밝기로 가른다."""
    r, g, b = p
    if max(p) < 90:
        return "line"
    if min(p) > 150 and max(p) - min(p) < 50:
        return None                  # 흰 글자
    if g - max(r, b) > 25:           # 초록 = 콜(림프). 청록이 파랑으로 새지 않게 파랑보다 먼저 본다
        return "call"
    if b - r > 30:
        return "fold"
    if r - b > 30:
        return "act"
    return None


def lum(p):
    return 0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2]


def split_reds(reds):
    """빨강 픽셀들의 밝기 → 올인/레이즈를 가르는 경계값 (한 톤뿐이면 None).

    GTO 툴은 **같은 빨강의 진하기로 액션을 구분한다** — 진한 쪽이 올인, 밝은 쪽이
    레이즈. 색을 고정하면 테마가 바뀔 때 깨지므로, 실제로 찍힌 밝기를 2-평균으로
    갈라 경계를 그때그때 정한다. 두 덩어리가 충분히 안 떨어지면 한 톤으로 본다."""
    vals = sorted(lum(p) for p in reds)
    if len(vals) < 50:
        return None
    lo, hi = vals[0], vals[-1]
    if hi - lo < MIN_TONE_GAP:
        return None
    a, b = lo, hi                                  # 2-평균 (초기값 = 양 끝)
    for _ in range(12):
        left = [v for v in vals if abs(v - a) <= abs(v - b)]
        right = [v for v in vals if abs(v - a) > abs(v - b)]
        if not left or not right:
            return None
        a, b = sum(left) / len(left), sum(right) / len(right)
    if b - a < MIN_TONE_GAP:                       # 한 톤이 번진 것뿐
        return None
    return (a + b) / 2


# ── 격자 찾기 ──────────────────────────────────────────────────────────────────

def grid_box(im, step=4):
    """차트 픽셀이 빽빽한 직사각형 영역 = 13×13 그리드. (x0, y0, x1, y1).

    step 간격으로 훑으므로 카운트는 **샘플 인덱스** 기준으로 모았다가 좌표로 되돌린다."""
    nx, ny = (im.w + step - 1) // step, (im.h + step - 1) // step
    cols, rows = [0] * nx, [0] * ny
    for yi in range(ny):
        y = yi * step
        for xi in range(nx):
            if kind(im.px(xi * step, y)) in ("fold", "act", "call"):
                cols[xi] += 1
                rows[yi] += 1

    def densest(counts):
        """차트 픽셀이 몰린 가장 긴 구간. **격자선에서 생기는 짧은 끊김은 이어 붙인다** —
        안 그러면 셀 한 칸이 그리드 전체로 잡힌다."""
        peak = max(counts)
        if peak == 0:
            raise GridNotFound("화면에서 레인지 그리드를 찾지 못했습니다. "
                             "차트가 보이는 상태인지 확인하거나 --region 으로 영역을 주세요.")
        lo, gap = peak * 0.5, 5                       # gap: 샘플 5칸(≈20px)까지는 같은 덩어리
        runs = []
        for i, v in enumerate(counts + [0]):
            if v >= lo:
                if runs and i - runs[-1][1] <= gap + 1:
                    runs[-1][1] = i
                else:
                    runs.append([i, i])
        if not runs:
            raise GridNotFound("화면에서 레인지 그리드를 찾지 못했습니다.")
        return max(runs, key=lambda r: r[1] - r[0])

    x0, x1 = densest(cols)
    y0, y1 = densest(rows)
    return x0 * step, y0 * step, x1 * step, y1 * step


def lines_in(im, box, horiz):
    """격자 구분선 위치. 셀 경계를 정확히 잡아야 '9%짜리 얇은 띠'도 살아난다."""
    x0, y0, x1, y1 = box
    rng = range(max(0, x0 - 10), min(im.w, x1 + 11)) if horiz else \
        range(max(0, y0 - 10), min(im.h, y1 + 11))
    other = (y0, y1) if horiz else (x0, x1)
    probes = [other[0] + (other[1] - other[0]) * i // 40 for i in range(1, 40)]

    hits, run, out = [], None, []
    for i in rng:
        dark = sum(1 for j in probes
                   if kind(im.px(i, j) if horiz else im.px(j, i)) == "line")
        hits.append(dark / len(probes) > 0.6)
    for n, is_line in enumerate(hits):
        i = rng[0] + n
        if is_line and run is None:
            run = i
        elif not is_line and run is not None:
            out.append((run + i - 1) // 2)
            run = None
    if run is not None:
        out.append((run + rng[-1]) // 2)
    return out


def cell_edges(im, box, horiz):
    """13칸의 경계 14개. 선 검출이 실패하면 등분으로 떨어진다(경고 출력)."""
    found = lines_in(im, box, horiz)
    if len(found) == 14:
        return found, True
    lo, hi = (box[0], box[2]) if horiz else (box[1], box[3])
    span = hi - lo
    return [lo + span * i // 13 for i in range(14)], False


# ── 그리드 읽기 ────────────────────────────────────────────────────────────────

def read_grid(im, box=None):
    """이미지 → ({조합: 액션 빈도}, {조합: 올인 빈도}, {조합: 콜 빈도}, 메모 dict).

    액션 빈도는 폴드가 아닌 전부(레이즈+올인+콜)의 합계다. 콜 열도 분모에 들어가야
    '레이즈 50 · 콜 50'인 칸이 레이즈 100%로 부풀지 않는다. `box`를 주면 격자 찾기를
    건너뛴다 (감시 모드가 첫 장에서 잡은 위치를 재사용)."""
    box = box or grid_box(im)
    vx, vok = cell_edges(im, box, True)
    hy, hok = cell_edges(im, box, False)
    notes = {"box": box, "lines_ok": vok and hok}

    # 1차: 셀마다 x열별 대표색을 모은다 (빨강은 픽셀 자체를 들고 있다가 나중에 가른다)
    grid = {}
    reds = []
    for r in range(13):
        for c in range(13):
            x0, x1, y0, y1 = vx[c], vx[c + 1], hy[r], hy[r + 1]
            ys = [y0 + (y1 - y0) * i // 12 for i in range(3, 12)]   # 글자 아래쪽만
            cols = []
            for x in range(x0 + 2, x1 - 1):
                tally, pix = {}, []
                for y in ys:
                    p = im.px(x, y)
                    k = kind(p)
                    if k in ("fold", "act", "call"):
                        tally[k] = tally.get(k, 0) + 1
                        if k == "act":
                            pix.append(p)
                if not tally:
                    continue
                top = max(tally, key=tally.get)
                if top == "act":
                    cols.append(pix[len(pix) // 2])         # 그 열의 대표 빨강
                    reds.append(pix[len(pix) // 2])
                elif top == "call":
                    cols.append(CALL)
                else:
                    cols.append(None)                       # 폴드
            grid[(r, c)] = cols

    # 2차: 빨강 전체의 밝기 분포로 올인/레이즈 경계를 정하고 열마다 배분
    cut = split_reds(reds)
    notes["two_tone"] = cut is not None
    freq, jam, call, mixed = {}, {}, {}, []
    for (r, c), cols in grid.items():
        if not cols:
            continue
        act = [p for p in cols if p is not None and p is not CALL]
        n_call = sum(1 for p in cols if p is CALL)
        f = (len(act) + n_call) / len(cols)
        lab = (RANKS[r] * 2 if r == c else
               RANKS[r] + RANKS[c] + "s" if c > r else RANKS[c] + RANKS[r] + "o")
        if f <= 0.005:
            continue
        freq[lab] = round(f, 3)
        if cut is not None:
            j = sum(1 for p in act if lum(p) < cut) / len(cols)
            if j > 0.005:
                jam[lab] = round(j, 3)
        if n_call / len(cols) > 0.005:
            call[lab] = round(n_call / len(cols), 3)
        if 0.25 < f < 0.75:
            mixed.append((lab, f))
    notes["mixed"] = mixed
    return freq, jam, call, notes


# ── 앱에 보내기 ────────────────────────────────────────────────────────────────

def to_text(freq):
    """parse_range가 읽는 `조합:퍼센트` 텍스트. 100%짜리가 있어 0~100 스케일로 인식된다."""
    return ", ".join(f"{k}:{v * 100:g}" for k, v in freq.items())


def existing(port, pos, slot, vs=None):
    """이미 가져온 차트가 그 슬롯에 있으면 그 정보 (같은 bb에 다시 넣으면 덮어쓴다)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/range/state", timeout=10) as r:
            st = json.loads(r.read().decode("utf-8"))
    except urllib.error.URLError:
        return None
    for c in st.get("custom") or []:
        if (c.get("pos") == pos and (c.get("vs") or None) == vs
                and str(c.get("stack")) == str(slot)):
            return c
    return None


def send(port, pos, stack, freq, jam, call, source, vs=None):
    body = json.dumps({"pos": pos, "vs": vs or "", "stack": stack, "text": to_text(freq),
                       "jam": to_text(jam) if jam else "",
                       "call": to_text(call) if call else "",
                       "source": source}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/range/import", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise SystemExit(f"앱에 연결하지 못했습니다 ({e}).\n"
                         f"  python3 gui.py  를 먼저 띄우고 다시 실행하세요 "
                         f"(다른 포트면 --port).\n"
                         f"  레인지 텍스트는 아래에 있으니 UI에 붙여넣어도 됩니다:\n\n"
                         f"{to_text(freq)}")


# ── 감시 모드 (--watch) ────────────────────────────────────────────────────────
# 280장(스택 8 × 오픈 7 + 방어 28)을 한 장씩 커맨드로 넣으면 대부분의 시간이 '터미널로 가서
# 인자 고쳐 치기'에 든다. 감시 모드는 순서표를 들고 화면을 폴링하다가 **새 차트가 뜨고 잠깐
# 그대로면** 순서표의 다음 칸으로 저장한다. 사용자는 GTO 툴에서 클릭만 하면 된다.
#
# 이 도구는 화면 글자를 못 읽으므로 '지금 이 차트가 BB vs CO인지'는 스스로 모른다 — 순서를
# 지키는 건 사용자 몫이다. 그래서 다음 칸을 크게 띄우고, 뻔히 이상한 차트(오픈 차트에 콜이 있다,
# BB 방어에 콜이 없다)는 저장하지 않고 멈추고, 방금 저장한 걸 되돌리는 키(u)를 둔다.
# 도구가 GTO 툴을 대신 클릭하지는 않는다 — 사이트를 자동으로 긁는 건 이용약관 문제가 될 수 있다.

POLL_SEC = 0.8        # 화면 확인 간격
STABLE_POLLS = 2      # 같은 차트가 이만큼 연달아 보여야 저장 (넘어가는 중간 화면을 피한다)


def plan(stacks, only=None, jams=()):
    """캡처 순서표 [(포지션, 스택, 상대 또는 None)]. GTO 툴에서 클릭해 가는 순서를 따른다:
    (스택마다) UTG 오픈 차트 → UTG 레이즈 후 UTG1·LJ…BB의 방어 차트 → (UTG가 그 스택에서
    올인으로도 연다면) UTG 올인 후 UTG1…BB의 방어 차트 → UTG1 오픈 차트 → …

    `jams` = 오픈 차트에 올인(진한 빨강)이 있는 (오프너, 스택) 집합. 올인을 받는 노드는 그때만
    존재하므로, 이미 가져온 오픈 차트를 보고 정한다 — 올인 오픈이 없는 스택에 빈 칸을 만들지 않게."""
    import ranges
    order = ranges.POS_8MAX
    out = []
    for st in stacks:
        for i, op in enumerate(order[:-1]):              # 오프너는 UTG~SB (BB는 오픈 기회가 없다)
            out.append((op, st, None))
            out.extend((resp, st, op) for resp in order[i + 1:])
            if (op, str(st)) in jams:
                out.extend((resp, st, op + ranges.ALLIN) for resp in order[i + 1:])
    return [t for t in out if not only or t[0] in only]


def fetch_state(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/range/state", timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise SystemExit(f"앱에 연결하지 못했습니다 ({e}). python3 gui.py 를 먼저 띄우세요.")


def delete_slot(port, pos, stack, vs):
    body = json.dumps({"pos": pos, "stack": stack, "vs": vs or ""}).encode("utf-8")
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/range/delete-chart", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def capture_fast():
    """감시용 캡처 — 메인 디스플레이를 BMP로 바로 찍는다 (sips 변환을 건너뛰어 빠르다)."""
    with tempfile.NamedTemporaryFile(suffix=".bmp", delete=False) as f:
        shot = f.name
    try:
        r = subprocess.run(["screencapture", "-x", "-t", "bmp", shot], capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"화면 캡처 실패: {r.stderr.strip()}\n"
                             "터미널에 '화면 기록' 권한이 필요할 수 있습니다.")
        with open(shot, "rb") as f:
            return Img(f.read())
    finally:
        os.unlink(shot)


def signature(freq, jam, call):
    """차트 비교용 지문. 같은 화면을 두 번 읽어도 같게 나오도록 2자리로 반올림."""
    r = lambda d: tuple(sorted((k, round(v, 2)) for k, v in d.items()))
    return r(freq), r(jam), r(call)


def suspicious(slot, freq, call):
    """순서가 어긋났을 때 흔히 생기는 모양이면 경고 문구 (아니면 None)."""
    import ranges
    pos, st, vs = slot
    if ranges.vs_parts(vs)[1]:
        return None          # 올인을 받은 차트는 콜 색이 툴마다 달라 모양으로 판단하지 않는다
    cw = lambda c: 6 if len(c) == 2 else 4 if c.endswith("s") else 12
    cpct = sum(v * cw(c) for c, v in call.items()) / 1326 * 100
    if not vs and pos != "SB" and cpct > 1:
        return f"오픈 차트인데 콜(초록)이 {cpct:.0f}% 보입니다 — 방어 화면이 떠 있는 것 아닌가요?"
    if vs and pos == "BB" and not call:
        return "BB 방어 차트인데 콜(초록)이 없습니다 — 오픈 화면이나 다른 자리가 떠 있는 것 아닌가요?"
    return None


def slot_name(slot):
    """감시 모드 안내용 이름. 방어 차트는 **액션 순서대로 오프너를 먼저** 쓴다 ('UTG vs BTN' =
    UTG가 열고 BTN이 받는다) — GTO 툴에서 클릭해 가는 순서와 같아 읽기 편하다. 앱의 다른 화면은
    'BTN vs UTG'(받는 쪽 먼저)이니 섞어 쓰지 않게 이 함수만 쓴다."""
    import ranges
    pos, st, vs = slot
    op, allin = ranges.vs_parts(vs)
    if op:
        return f"{op}{' 올인' if allin else ''} vs {pos} · {st}bb"
    return f"{pos} 오픈 · {st}bb"


def slot_hint(slot):
    """GTO 툴에서 클릭할 길. 폴드하는 건 **사이에 낀 자리들**이라 그 범위를 그대로 쓴다
    ('UTG1~HJ 폴드'). 사이에 아무도 없으면 폴드 단계를 빼고 바로 차례로 넘어간다."""
    import ranges
    order = ranges.POS_8MAX
    pos, st, vs = slot
    op, allin = ranges.vs_parts(vs)
    folds = order[order.index(op) + 1 if op else 0:order.index(pos)]
    fold = (f"{folds[0]} 폴드 → " if len(folds) == 1 else
            f"{folds[0]}~{folds[-1]} 폴드 → " if folds else "")
    if op:
        return f"GTO 툴: {op} {'올인' if allin else '레이즈'} → {fold}{pos} 차례"
    return f"GTO 툴: {fold}{pos} 차례" + (" (첫 액션)" if not folds else "")


def read_line(timeout):
    """폴링 사이에 키 입력 확인 (Enter 친 줄 하나, 없으면 None). 표준 라이브러리 select."""
    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    return sys.stdin.readline() if ready else None


def watch(a):
    import ranges
    st = fetch_state(a.port)
    have = {(c["pos"], c.get("vs") or None, str(c["stack"])) for c in st.get("custom") or []}
    if a.stacks:
        stacks = [s.strip() for s in a.stacks.split(",") if s.strip()]
    else:                                   # 기본 = 이미 가져온 차트들의 bb 목록
        stacks = sorted({str(c["bb"]) for c in st.get("custom") or [] if c.get("bb") is not None},
                        key=float)
    for s in stacks:
        if parse_stack(s)[1] is None:
            raise SystemExit(f"--stacks 에는 bb 숫자만 씁니다 (받은 값: {s})")
    if not stacks:
        raise SystemExit("스택 목록이 없습니다 — --stacks 13,15,20 처럼 주세요.")
    only = {ranges._norm_pos(p) for p in a.only.split(",")} if a.only else None
    jams = {(c["pos"], str(c["stack"])) for c in st.get("custom") or []
            if not c.get("vs") and c.get("has_jam") and c.get("jam_pct", 0) > 0.05}
    full = plan(stacks, only, jams)
    todo = [t for t in full if (t[0], t[2], t[1]) not in have]
    print(f"감시 모드 — 스택 {', '.join(stacks)}bb · 전체 {len(full)}장 중 이미 있는 "
          f"{len(full) - len(todo)}장 건너뜀 → 남은 {len(todo)}장")
    if not todo:
        print("모두 들어 있습니다.")
        return
    print("GTO 툴에서 아래 안내대로 차트를 띄우면 자동으로 찍어 저장합니다 (마우스는 그리드 밖에).\n"
          "  Enter = 지금 화면을 이 칸으로 저장 (첫 장, 또는 앞 칸과 똑같은 차트일 때)\n"
          "  u = 방금 저장한 것 되돌리기 · s = 이 칸 건너뛰기 · q = 그만 (다음에 남은 것부터 이어서)")

    box, last_sig, pending, held, n_same = None, None, None, None, 0
    # 되돌리기·건너뛰기 직후엔 화면에 남아 있는 차트를 '이미 본 것'으로 다시 친다 — 안 그러면
    # 그 차트가 방금 열린 칸으로 곧장 저장된다 (화면은 아무것도 안 바뀌었는데)
    reseed = False
    saved = []                               # 되돌리기용 [(todo 인덱스, 슬롯)]
    # 시작 화면에 떠 있는 차트는 '이미 본 것'으로 친다 — 첫 칸으로 잘못 찍히지 않게
    try:
        im = capture_fast()
        freq, jam, call, notes = read_grid(im)
        if freq:
            last_sig, box = signature(freq, jam, call), notes["box"]
    except GridNotFound:
        pass

    i = 0

    def announce():
        print(f"\n──── [{i + 1}/{len(todo)}] 다음 ▶ {slot_name(todo[i])}\n     {slot_hint(todo[i])}")

    announce()
    while i < len(todo):
        line = read_line(POLL_SEC)
        force = False
        if line is not None:
            c = line.strip().lower()
            if c == "q":
                break
            if c == "s":
                print(f"   ⏭  {slot_name(todo[i])} 건너뜀")
                i += 1
                pending = held = None
                reseed = True
                if i < len(todo):
                    announce()
                continue
            if c == "u":
                if not saved:
                    print("   되돌릴 게 없습니다")
                    continue
                j, slot = saved.pop()
                delete_slot(a.port, slot[0], slot[1], slot[2])
                print(f"   ↩️  {slot_name(slot)} 지웠습니다 — 그 칸부터 다시")
                i, pending, held = j, None, None
                reseed = True
                announce()
                continue
            force = c == ""
        try:
            im = capture_fast()
            freq, jam, call, notes = read_grid(im, box)
            if box and not notes["lines_ok"]:          # 화면이 움직였다 — 격자를 새로 찾는다
                freq, jam, call, notes = read_grid(im)
        except GridNotFound:
            box = None
            if force:
                print("   화면에서 그리드를 찾지 못했습니다")
            continue
        if not freq:
            box = None
            continue
        box = notes["box"]
        sig = signature(freq, jam, call)
        slot = todo[i]
        if reseed and not force:
            last_sig, reseed = sig, False
            continue
        reseed = False
        if not force:
            if sig == last_sig or sig == held:          # 아직 앞 차트 그대로 / 경고 띄운 채 대기 중
                continue
            if sig != pending:                          # 새 차트를 처음 봤다 — 한 번 더 같아야 저장
                pending, n_same = sig, 1
                continue
            n_same += 1
            if n_same < STABLE_POLLS:
                continue
            warn = suspicious(slot, freq, call)
            if warn:
                held = sig
                print(f"   ⚠️  {warn}\n      맞으면 Enter로 저장, 아니면 화면을 고치세요")
                continue
        res = send(a.port, slot[0], slot[1], freq, jam, call,
                   f"GTOWizard {ranges.spot_name(slot[0], slot[2])} {slot[1]}bb", slot[2])
        if res.get("error"):
            print(f"   ❌ 저장 실패: {res['error']}")
            held = sig
            continue
        extra = []
        cw = lambda c: 6 if len(c) == 2 else 4 if c.endswith("s") else 12
        if call:
            extra.append(f"콜 {sum(v * cw(k) for k, v in call.items()) / 1326 * 100:.0f}%")
        if jam:
            extra.append(f"올인 {sum(v * cw(k) for k, v in jam.items()) / 1326 * 100:.0f}%")
        print(f"   ✅ {slot_name(slot)} 저장 — 액션 {res['pct']}%"
              + (f" ({' · '.join(extra)})" if extra else "")
              + ("" if notes["lines_ok"] else "  ⚠️ 격자선을 못 찾아 등분으로 읽음"))
        saved.append((i, slot))
        last_sig, pending, held = sig, None, None
        i += 1
        if i < len(todo):
            announce()
    left = len(todo) - i
    print(f"\n끝 — 이번에 {len(saved)}장 저장" + (f", 남은 {left}장은 다음에 --watch 로 이어서" if left else ""))


def main():
    ap = argparse.ArgumentParser(description="GTO 툴 화면의 레인지 그리드를 캡처해서 차트로 가져온다")
    ap.add_argument("pos", nargs="?", help="포지션: UTG UTG1 LJ HJ CO BTN SB BB")
    ap.add_argument("stack", nargs="?",
                    help="스택: GTO 툴의 bb 숫자 (20, 12.5, 100 …) 또는 pf/short/mid/deep")
    ap.add_argument("--watch", action="store_true",
                    help="감시 모드: 순서표대로 화면에 뜨는 차트를 자동으로 연달아 저장")
    ap.add_argument("--stacks", help="감시 모드 스택 목록 (예: 13,15,20). 기본 = 이미 가져온 차트들의 bb")
    ap.add_argument("--only", help="감시 모드에서 이 포지션 차트만 (예: BB 또는 SB,BB)")
    ap.add_argument("--vs", help="방어 차트의 오프너 (예: BTN). BB는 필수")
    ap.add_argument("--allin", action="store_true", help="--vs의 오프너가 올인으로 열었을 때의 차트")
    ap.add_argument("--image", help="화면 캡처 대신 이 이미지 파일에서 읽기")
    ap.add_argument("--region", help="캡처 영역 x,y,w,h")
    ap.add_argument("--delay", type=float, default=0, help="캡처 전 대기 초")
    ap.add_argument("--source", default="", help="출처 메모 (예: 'GTOWizard 8max LJ 20bb')")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--dry-run", action="store_true", help="읽기만 하고 앱에 보내지 않음")
    a = ap.parse_args()
    if a.watch:
        return watch(a)
    if not a.pos or not a.stack:
        ap.error("포지션과 스택을 주세요 (예: grab_chart.py CO 30) — 또는 --watch")

    pos = parse_pos(a.pos)
    if not pos:
        import ranges
        raise SystemExit(f"포지션은 {' / '.join(ranges.POS_8MAX)} 중 하나여야 합니다 "
                         f"(받은 값: {a.pos})")
    import ranges
    if a.allin and not a.vs:
        raise SystemExit("--allin 은 --vs 와 같이 씁니다 (예: --vs UTG --allin)")
    vs, err = ranges.parse_vs(pos, (a.vs + ranges.ALLIN) if a.allin else a.vs)
    if err:                                         # 캡처 전에 막는다 (찍고 나서 거절당하지 않게)
        raise SystemExit(err)
    slot, bb, bucket = parse_stack(a.stack)
    if not slot:
        raise SystemExit(f"스택은 bb 숫자(예: 20) 또는 {' / '.join(BUCKETS)} 중 하나여야 합니다 "
                         f"(받은 값: {a.stack})")
    label = f"{bb}bb" if bb is not None else BUCKET_LABEL[bucket]
    spot = ranges.spot_name(pos, vs)
    print(f"→ {spot} · {label} 슬롯"
          + (f" (채점 구간 {BUCKET_LABEL[bucket]})" if bb is not None else ""))

    im = load_image(os.path.expanduser(a.image)) if a.image else capture(a.region, a.delay)
    freq, jam, call, notes = read_grid(im)
    if not freq:
        raise SystemExit("그리드는 찾았지만 액션 색을 하나도 읽지 못했습니다. --region 으로 영역을 좁혀 보세요.")
    if not notes["lines_ok"]:
        print("⚠️  격자 구분선을 정확히 찾지 못해 등분으로 읽었습니다 — 빈도가 조금 어긋날 수 있습니다.")

    cw = lambda c: 6 if len(c) == 2 else 4 if c.endswith("s") else 12
    pct = sum(v * cw(c) for c, v in freq.items()) / 1326 * 100
    jpct = sum(v * cw(c) for c, v in jam.items()) / 1326 * 100
    cpct = sum(v * cw(c) for c, v in call.items()) / 1326 * 100
    print(f"읽음: 비폴드 {len(freq)}조합 · 가중 액션 {pct:.1f}%  (영역 {notes['box']})")
    if call:
        print(f"  초록(콜·림프) 감지 — 콜 {cpct:.1f}% ({len(call)}조합) · 드릴은 "
              f"{'3벳' if vs else '오픈'}/콜/폴드 3지선다가 됩니다")
    if notes["two_tone"]:
        print(f"  빨강 두 톤 감지 — {'3벳' if vs else '레이즈'} {pct - jpct - cpct:.1f}% · 올인 {jpct:.1f}% "
              f"({len(jam)}조합에 올인 섞임)")
    else:
        print("  빨강이 한 톤이라 전부 같은 액션으로 읽었습니다 "
              "(레이즈/올인이 나뉜 차트면 --region 으로 그리드만 잡아 보세요)")
    if notes["mixed"]:
        print("  경계(혼합) 핸드: " +
              ", ".join(f"{k} {v * 100:.0f}%" for k, v in notes["mixed"]))

    if a.dry_run:
        print("\n[액션 합계] " + to_text(freq))
        if jam:
            print("\n[올인] " + to_text(jam))
        if call:
            print("\n[콜] " + to_text(call))
        return
    old = existing(a.port, pos, slot, vs)
    if old:
        print(f"⚠️  이 슬롯엔 이미 차트가 있습니다 — 덮어씁니다 "
              f"(기존: 오픈 {old['pct']}% · {old.get('source') or '출처 없음'} · {old.get('ts')})")
    res = send(a.port, pos, slot, freq, jam, call, a.source or f"GTOWizard {spot} {label}", vs)
    if res.get("error"):
        raise SystemExit(f"임포트 실패: {res['error']}")
    print(f"✅ {spot} · {res['label']} 슬롯에 저장 — "
          f"액션 {res['pct']}% (경계 {res['mix_pct']}%) · {res['n']}조합")
    if res.get("warnings"):
        print("   읽지 못한 토큰:", ", ".join(res["warnings"]))


if __name__ == "__main__":
    main()
