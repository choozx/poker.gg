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

포지션: UTG UTG1 LJ HJ CO BTN SB BB (8맥스, `ranges.POS_8MAX`. 소문자·UTG+1도 받는다)
스택:   GTO 툴에 적힌 bb 숫자를 그대로 준다 (13, 20, 100 …). 버킷 키(pf/short/mid/deep)도 받는다.

**bb 숫자는 그대로 슬롯이 된다** — 10bb와 13bb 차트가 따로 산다. 드릴 채점은 여전히 4버킷
단위라, 한 버킷에 여러 장이 있으면 그 구간에서 실제로 가장 흔한 스택에 가까운 차트가 쓰인다
(`ranges._BUCKET_MID`). 같은 bb에 다시 넣으면 덮어쓰고, 그때는 기존 차트 정보를 먼저 알려준다.

표준 라이브러리만 쓴다. 화면 캡처는 맥 기본 `screencapture`, 이미지 디코드는 BMP를
직접 읽는다(PNG 등은 맥 기본 `sips`로 BMP 변환). 색 판정은 팔레트를 고정하지 않는다:
**파랑 계열=폴드 / 빨강 계열=액션**으로 가른 뒤, 빨강의 **진하기**를 2-평균으로 다시 갈라
진한 쪽을 올인, 밝은 쪽을 레이즈로 본다(`split_reds`). 올인 빈도는 `jam`으로 따로 보내
차트에 두 톤 그대로 그려진다 — 다만 **채점은 합계 기준**이다 (오픈이냐 폴드냐만 묻는다).
"""
import argparse
import json
import os
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

RANKS = "AKQJT98765432"
MIN_TONE_GAP = 30          # 빨강 두 톤으로 보려면 밝기가 이만큼은 떨어져 있어야 한다
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
    """픽셀 한 점 → 'fold'(파랑) / 'act'(빨강) / 'line'(어두움) / None(글자·그 외).

    팔레트를 고정하지 않는다. 툴 테마가 바뀌어 빨강·파랑의 정확한 값이 달라져도 동작한다.
    빨강 두 톤(레이즈/올인)은 여기서 나누지 않고 `split_reds`가 밝기로 가른다."""
    r, g, b = p
    if max(p) < 90:
        return "line"
    if min(p) > 150 and max(p) - min(p) < 50:
        return None                  # 흰 글자
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
            if kind(im.px(xi * step, y)) in ("fold", "act"):
                cols[xi] += 1
                rows[yi] += 1

    def densest(counts):
        """차트 픽셀이 몰린 가장 긴 구간. **격자선에서 생기는 짧은 끊김은 이어 붙인다** —
        안 그러면 셀 한 칸이 그리드 전체로 잡힌다."""
        peak = max(counts)
        if peak == 0:
            raise SystemExit("화면에서 레인지 그리드를 찾지 못했습니다. "
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
            raise SystemExit("화면에서 레인지 그리드를 찾지 못했습니다.")
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

def read_grid(im):
    """이미지 → ({조합: 액션 빈도}, {조합: 올인 빈도}, 메모 dict)."""
    box = grid_box(im)
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
                    if k in ("fold", "act"):
                        tally[k] = tally.get(k, 0) + 1
                        if k == "act":
                            pix.append(p)
                if not tally:
                    continue
                if max(tally, key=tally.get) == "act":
                    cols.append(pix[len(pix) // 2])         # 그 열의 대표 빨강
                    reds.append(pix[len(pix) // 2])
                else:
                    cols.append(None)                       # 폴드
            grid[(r, c)] = cols

    # 2차: 빨강 전체의 밝기 분포로 올인/레이즈 경계를 정하고 열마다 배분
    cut = split_reds(reds)
    notes["two_tone"] = cut is not None
    freq, jam, mixed = {}, {}, []
    for (r, c), cols in grid.items():
        if not cols:
            continue
        act = [p for p in cols if p is not None]
        f = len(act) / len(cols)
        lab = (RANKS[r] * 2 if r == c else
               RANKS[r] + RANKS[c] + "s" if c > r else RANKS[c] + RANKS[r] + "o")
        if f <= 0.005:
            continue
        freq[lab] = round(f, 3)
        if cut is not None:
            j = sum(1 for p in act if lum(p) < cut) / len(cols)
            if j > 0.005:
                jam[lab] = round(j, 3)
        if 0.25 < f < 0.75:
            mixed.append((lab, f))
    notes["mixed"] = mixed
    return freq, jam, notes


# ── 앱에 보내기 ────────────────────────────────────────────────────────────────

def to_text(freq):
    """parse_range가 읽는 `조합:퍼센트` 텍스트. 100%짜리가 있어 0~100 스케일로 인식된다."""
    return ", ".join(f"{k}:{v * 100:g}" for k, v in freq.items())


def existing(port, pos, slot):
    """이미 가져온 차트가 그 슬롯에 있으면 그 정보 (같은 bb에 다시 넣으면 덮어쓴다)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/range/state", timeout=10) as r:
            st = json.loads(r.read().decode("utf-8"))
    except urllib.error.URLError:
        return None
    for c in st.get("custom") or []:
        if c.get("pos") == pos and str(c.get("stack")) == str(slot):
            return c
    return None


def send(port, pos, stack, freq, jam, source):
    body = json.dumps({"pos": pos, "stack": stack, "text": to_text(freq),
                       "jam": to_text(jam) if jam else "",
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


def main():
    ap = argparse.ArgumentParser(description="GTO 툴 화면의 레인지 그리드를 캡처해서 차트로 가져온다")
    ap.add_argument("pos", help="포지션: UTG UTG1 LJ HJ CO BTN SB BB")
    ap.add_argument("stack", help="스택: GTO 툴의 bb 숫자 (20, 12.5, 100 …) 또는 pf/short/mid/deep")
    ap.add_argument("--image", help="화면 캡처 대신 이 이미지 파일에서 읽기")
    ap.add_argument("--region", help="캡처 영역 x,y,w,h")
    ap.add_argument("--delay", type=float, default=0, help="캡처 전 대기 초")
    ap.add_argument("--source", default="", help="출처 메모 (예: 'GTOWizard 8max LJ 20bb')")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--dry-run", action="store_true", help="읽기만 하고 앱에 보내지 않음")
    a = ap.parse_args()

    pos = parse_pos(a.pos)
    if not pos:
        import ranges
        raise SystemExit(f"포지션은 {' / '.join(ranges.POS_8MAX)} 중 하나여야 합니다 "
                         f"(받은 값: {a.pos})")
    slot, bb, bucket = parse_stack(a.stack)
    if not slot:
        raise SystemExit(f"스택은 bb 숫자(예: 20) 또는 {' / '.join(BUCKETS)} 중 하나여야 합니다 "
                         f"(받은 값: {a.stack})")
    label = f"{bb}bb" if bb is not None else BUCKET_LABEL[bucket]
    print(f"→ {pos} · {label} 슬롯"
          + (f" (채점 구간 {BUCKET_LABEL[bucket]})" if bb is not None else ""))

    im = load_image(os.path.expanduser(a.image)) if a.image else capture(a.region, a.delay)
    freq, jam, notes = read_grid(im)
    if not freq:
        raise SystemExit("그리드는 찾았지만 액션 색을 하나도 읽지 못했습니다. --region 으로 영역을 좁혀 보세요.")
    if not notes["lines_ok"]:
        print("⚠️  격자 구분선을 정확히 찾지 못해 등분으로 읽었습니다 — 빈도가 조금 어긋날 수 있습니다.")

    cw = lambda c: 6 if len(c) == 2 else 4 if c.endswith("s") else 12
    pct = sum(v * cw(c) for c, v in freq.items()) / 1326 * 100
    jpct = sum(v * cw(c) for c, v in jam.items()) / 1326 * 100
    print(f"읽음: 비폴드 {len(freq)}조합 · 가중 액션 {pct:.1f}%  (영역 {notes['box']})")
    if notes["two_tone"]:
        print(f"  빨강 두 톤 감지 — 레이즈 {pct - jpct:.1f}% · 올인 {jpct:.1f}% "
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
        return
    old = existing(a.port, pos, slot)
    if old:
        print(f"⚠️  이 슬롯엔 이미 차트가 있습니다 — 덮어씁니다 "
              f"(기존: 오픈 {old['pct']}% · {old.get('source') or '출처 없음'} · {old.get('ts')})")
    res = send(a.port, pos, slot, freq, jam, a.source or f"GTOWizard {pos} {label}")
    if res.get("error"):
        raise SystemExit(f"임포트 실패: {res['error']}")
    print(f"✅ {res['pos']} · {res['label']} 슬롯에 저장 — "
          f"액션 {res['pct']}% (경계 {res['mix_pct']}%) · {res['n']}조합")
    if res.get("warnings"):
        print("   읽지 못한 토큰:", ", ".join(res["warnings"]))


if __name__ == "__main__":
    main()
