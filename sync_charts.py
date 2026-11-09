#!/usr/bin/env python3
"""S3의 GTO 솔루션 DB로 가져온 차트를 통째로 바꾼다 (선택 도구).

    python3 gui.py --no-browser &      # 앱이 떠 있어야 한다
    python3 sync_charts.py --dry-run   # 몇 장이 나오는지만 본다
    python3 sync_charts.py             # 교체

앱을 켤 때도 `auto_sync`가 백그라운드에서 같은 일을 한다 (S3 객체가 바뀌었을 때만).

poker-tracker가 S3에 올린 SQLite(`pcs/*.db.gz`, 옛 `gtow-solutions.db.gz`)를 받아 차트로 바꾼 뒤
`POST /api/range/replace-charts`로 보낸다 — 저장·클라우드 푸시는 앱이 한다 (DB를 직접 쓰면 다음 실행의
클라우드 pull에 덮어쓰인다). 버킷 주소와 프로필은 poker-tracker의 `.local.env`에서 읽는다: 이 repo는
공개이므로 주소도, 받은 DB도 repo 안에 두지 않는다 (임시 폴더에 받고 끝나면 지운다).

쓰는 노드: chipEV(8m·7m), 전원 같은 스택, 앞선 액션이 없거나 한 번뿐인 프리플랍 노드.
"""
import argparse
import configparser
import datetime
import gzip
import hashlib
import hmac
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import ranges

# 예전 grab_chart 수집 대상 + 40·50bb. 더 촘촘히 넣으면 리크 리포트의 bb 차트별 표본이 쪼개진다
STACKS = (7, 10, 13, 15, 20, 25, 28, 30, 32, 35, 40, 50)
ENV_FILES = ("~/toyProj/poker-tracker/.local.env", "~/toy/poker-tracker/.local.env")  # PC마다 repo 위치가 다르다


def default_env_file(candidates=ENV_FILES):
    """있는 첫 파일 — 하나도 없으면 첫 후보(없는 파일이라 open_s3가 'S3 안 쓰는 PC'로 본다)."""
    paths = [os.path.expanduser(p) for p in candidates]
    return next((p for p in paths if os.path.isfile(p)), paths[0])


ENV_FILE = default_env_file()
SOURCE ="GTO Wizard 솔루션 DB"
ETAG_FILE = os.path.expanduser("~/.cache/analyze_hand_history/chart_sync_etags.json")
MIN_KEEP = 0.5              # 자동 교체로 차트가 이 비율 아래로 줄면 사고로 보고 멈춘다
ANTE = 0.125                # 솔루션의 depth는 bb + 앤티 몫 ("20.125" = 20bb)
_SEATS = {"MTTGeneral_8m": ranges.POS_8MAX,
          # 7맥스 UTG는 뒤에 6명이라 8맥스 UTG1과 같은 자리다 (ranges._BEHIND_8MAX와 같은 기준)
          "MTTGeneral_7m": ranges.POS_8MAX[1:]}


class SyncError(Exception):
    """사용자에게 그대로 보여 줄 수 있는 동기화 실패."""


# ---------------------------------------------------------------------------
# 솔루션 노드 → 차트
# ---------------------------------------------------------------------------

def _opener_vs(seat, code, bb):
    """앞선 액션 하나 → `vs` 표기. 10bb의 R10처럼 코드가 RAI가 아니어도 스택만큼이면 올인이다."""
    if code == "C":
        return f"{seat}-limp" if seat == "SB" else None    # MTT 트리의 오픈 림프는 SB뿐 (parse_vs와 같은 규칙)
    if code == "RAI" or float(code[1:]) >= bb:
        return f"{seat}-allin"
    return seat


def node_spot(gametype, depth, stacks, preflop_actions):
    """(bb, 자리, vs 또는 None) — 차트 모델에 넣을 수 없는 노드면 None."""
    seats = _SEATS.get(gametype)
    if not seats or len(set(stacks.split("-"))) != 1:
        return None
    bb = float(depth) - ANTE
    if not bb.is_integer():
        return None
    actions = preflop_actions.split("-") if preflop_actions else []
    acted = [(i, a) for i, a in enumerate(actions) if a != "F"]
    if len(acted) > 1 or len(actions) >= len(seats):
        return None
    vs = None
    if acted:
        vs = _opener_vs(seats[acted[0][0]], acted[0][1], bb)
        if vs is None:
            return None
    return int(bb), seats[len(actions)], vs


def _text(freqs):
    # 부동소수 합이 1을 살짝 넘으면 parse_range가 0~100 스케일로 오해하므로 1로 깎는다
    return ", ".join(f"{h}:{min(f, 1.0):.4f}" for h, f in sorted(freqs.items()) if f >= 0.00005)


def body_to_item(body, bb, pos, vs):
    """솔루션 응답 → `ranges.replace_charts` 항목. 액션 빈도가 전혀 없으면 None."""
    hands = list(body["players_info"][0]["simple_hand_counters"])     # strategy[] 169칸의 순서가 이 키 순서다
    total, jam, call = {}, {}, {}
    for sol in body["action_solutions"]:
        act = sol["action"]
        if act["type"] in ("FOLD", "CHECK"):
            continue
        for hand, f in zip(hands, sol["strategy"]):
            if f <= 0:
                continue
            total[hand] = total.get(hand, 0.0) + f
            if act["type"] == "CALL":
                call[hand] = call.get(hand, 0.0) + f
            elif act.get("allin"):
                jam[hand] = jam.get(hand, 0.0) + f
    text = _text(total)
    if not text:
        return None
    return {"pos": pos, "stack": str(bb), "vs": vs, "text": text, "jam": _text(jam), "call": _text(call)}


def read_db(path, stacks, found):
    """솔루션 DB 하나를 읽어 `found[(bb, 자리, vs)] = (8맥스인가, 받은 시각, 본문 gzip)`에 합친다.
    같은 칸은 8맥스 노드가 이기고, 그다음은 나중에 받은 것이 이긴다."""
    con = sqlite3.connect(f"file:{urllib.parse.quote(path)}?mode=ro", uri=True)
    try:
        cur = con.execute("SELECT gametype, depth, stacks, preflop_actions, body_gz, fetched_at FROM solution "
                          "WHERE street='PREFLOP' AND flop_actions='' AND board=''")
        for gametype, depth, st, pf, body, ts in cur:
            spot = node_spot(gametype, depth, st, pf)
            if not spot or spot[0] not in stacks:
                continue
            rank = (gametype.endswith("8m"), ts)
            if spot not in found or found[spot][0] < rank:
                found[spot] = (rank, body)
    finally:
        con.close()


def build_items(found):
    items = []
    for (bb, pos, vs), (_, body) in found.items():
        item = body_to_item(json.loads(gzip.decompress(body)), bb, pos, vs)
        if item:
            items.append(item)
    return items


# ---------------------------------------------------------------------------
# S3 (읽기 전용, SigV4)
# ---------------------------------------------------------------------------

def read_env(path):
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"\s*(?:export\s+)?([A-Z0-9_]+)=(.*)", line)
            if m:
                out[m.group(1)] = m.group(2).strip().strip("'\"")
    return out


def credentials(env):
    get = lambda k: os.environ.get(k) or env.get(k)
    if get("AWS_ACCESS_KEY_ID") and get("AWS_SECRET_ACCESS_KEY"):
        return get("AWS_ACCESS_KEY_ID"), get("AWS_SECRET_ACCESS_KEY"), get("AWS_SESSION_TOKEN")
    profile = get("AWS_PROFILE") or "default"
    cp = configparser.RawConfigParser()
    cp.read(os.path.expanduser("~/.aws/credentials"))
    if profile not in cp or "aws_access_key_id" not in cp[profile]:
        raise SyncError(f"AWS 키를 찾지 못했습니다 (프로필 '{profile}', ~/.aws/credentials)")
    s = cp[profile]
    return s["aws_access_key_id"], s["aws_secret_access_key"], s.get("aws_session_token")


class S3:
    EMPTY = hashlib.sha256(b"").hexdigest()

    def __init__(self, bucket, region, creds):
        self.bucket, self.region, self.creds = bucket, region, creds
        self.host = f"{bucket}.s3.{region}.amazonaws.com"

    def _open(self, path, query=None):
        ak, sk, token = self.creds
        now = datetime.datetime.now(datetime.timezone.utc)
        stamp, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
        headers = {"host": self.host, "x-amz-content-sha256": self.EMPTY, "x-amz-date": stamp}
        if token:
            headers["x-amz-security-token"] = token
        uri = urllib.parse.quote(path, safe="/-_.~")
        qs = "&".join(f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(v, safe='-_.~')}"
                      for k, v in sorted((query or {}).items()))
        names = sorted(headers)
        canon = "\n".join(["GET", uri, qs, "".join(f"{n}:{headers[n]}\n" for n in names),
                           ";".join(names), self.EMPTY])
        scope = f"{day}/{self.region}/s3/aws4_request"
        to_sign = "\n".join(["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canon.encode()).hexdigest()])
        key = ("AWS4" + sk).encode()
        for part in (day, self.region, "s3", "aws4_request"):
            key = hmac.new(key, part.encode(), hashlib.sha256).digest()
        sig = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
        send = {n: v for n, v in headers.items() if n != "host"}
        send["Authorization"] = (f"AWS4-HMAC-SHA256 Credential={ak}/{scope}, "
                                 f"SignedHeaders={';'.join(names)}, Signature={sig}")
        url = f"https://{self.host}{uri}" + (f"?{qs}" if qs else "")
        return urllib.request.urlopen(urllib.request.Request(url, headers=send), timeout=120)

    def list(self, prefix):
        """{객체 키: ETag}"""
        objs, token = {}, None
        while True:
            q = {"list-type": "2", "prefix": prefix}
            if token:
                q["continuation-token"] = token
            root = ET.fromstring(self._open("/", q).read())
            ns = {"s": root.tag.split("}")[0].strip("{")}
            objs.update((c.findtext("s:Key", "", ns), c.findtext("s:ETag", "", ns))
                        for c in root.findall("s:Contents", ns))
            token = root.findtext("s:NextContinuationToken", None, ns)
            if not token:
                return objs

    def download_gz(self, key, dest):
        with self._open("/" + key) as r, gzip.GzipFile(fileobj=r) as gz, open(dest, "wb") as f:
            shutil.copyfileobj(gz, f, 1 << 20)


def open_s3(env_file):
    """(S3, 경로) — 이 PC에 S3 설정이 없으면 None."""
    try:
        env = read_env(env_file)
    except OSError:
        return None
    m = re.match(r"s3://([^/]+)/(.*)$", env.get("POKER_TRACKER_S3_URI", ""))
    if not m:
        return None
    bucket, prefix = m.group(1), m.group(2)
    if prefix and not prefix.endswith("/"):
        prefix = prefix.rsplit("/", 1)[0] + "/"
    return S3(bucket, env.get("POKER_TRACKER_S3_REGION") or "ap-northeast-2", credentials(env)), prefix


def list_objects(s3, prefix):
    return {k: e for k, e in s3.list(prefix).items() if k.endswith(".db.gz")}


def fetch_items(s3, keys, stacks, log=print):
    found = {}
    with tempfile.TemporaryDirectory(prefix="sync_charts_") as tmp:
        for i, key in enumerate(keys):
            log(f"받는 중: {key}")
            path = os.path.join(tmp, f"{i}.db")
            s3.download_gz(key, path)
            read_db(path, stacks, found)
            os.remove(path)
    return build_items(found)


def _read_etags(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_etags(path, state):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def _auto_sync(db, persist, env_file, etag_file, log):
    opened = open_s3(env_file)
    if opened is None:
        return                      # S3를 안 쓰는 PC — 기능이 꺼진 것일 뿐이라 알리지 않는다
    s3, prefix = opened
    objects = list_objects(s3, prefix)
    if not objects:
        log("⚠️  차트 동기화: S3에 솔루션 DB가 없어 기존 차트를 유지합니다")
        return
    state = {"objects": objects, "stacks": sorted(STACKS)}
    current = len(ranges._charts(db))
    if current and _read_etags(etag_file) == state:
        log(f"📐 차트 동기화: S3 변경 없음 ({current}장)")
        return
    items = fetch_items(s3, sorted(objects), set(STACKS), log=lambda m: log(f"📐 차트 동기화: {m}"))
    # 사람이 돌리는 CLI와 달리 아무도 안 보는 동안 지우는 것이라, 크게 줄어드는 결과는 받지 않는다
    if len(items) < current * MIN_KEEP:
        log(f"⚠️  차트 동기화: {current}장 → {len(items)}장으로 크게 줄어 교체하지 않았습니다 "
            "(의도한 것이면 python3 sync_charts.py)")
        return
    resp = ranges.replace_charts(db, items, SOURCE)
    if not resp.get("ok"):
        log(f"⚠️  차트 동기화 실패 (기존 차트 유지): {resp.get('error')}")
        return
    if resp["changed"]:
        persist(db)
        log(f"📐 차트 동기화: {resp['removed']}장 → {resp['n']}장으로 교체")
    else:
        log(f"📐 차트 동기화: 내용 변경 없음 ({resp['n']}장)")
    _write_etags(etag_file, state)      # 끝까지 성공했을 때만 — 실패하면 다음 실행 때 다시 시도한다


def auto_sync(db, persist, env_file=ENV_FILE, etag_file=ETAG_FILE, log=print):
    """앱 시작 시 백그라운드 스레드에서 호출. 어떤 실패든 기존 차트로 앱은 계속 돈다."""
    try:
        _auto_sync(db, persist, env_file, etag_file, log)
    except Exception as e:          # 네트워크·권한·손상된 DB 전부 — 스레드에서 조용히 죽지 않게 알린다
        log(f"⚠️  차트 동기화 실패 (기존 차트 유지): {e}")


def post(port, items):
    data = json.dumps({"charts": items, "source": SOURCE}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/range/replace-charts", data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode("utf-8"))
    except urllib.error.URLError as e:
        sys.exit(f"앱에 연결하지 못했습니다 (python3 gui.py 를 먼저 띄우세요): {e.reason}")


def main():
    ap = argparse.ArgumentParser(description="S3 솔루션 DB로 차트 전체 교체")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--stacks", help="가져올 bb, 콤마로 (기본: " + ",".join(map(str, STACKS)) + ")")
    ap.add_argument("--env-file", default=ENV_FILE, help="S3 설정 파일 (poker-tracker의 .local.env)")
    ap.add_argument("--dry-run", action="store_true", help="변환 결과만 보고 앱에 보내지 않는다")
    args = ap.parse_args()
    stacks = {int(s) for s in args.stacks.split(",")} if args.stacks else set(STACKS)

    try:
        opened = open_s3(args.env_file)
        if opened is None:
            sys.exit(f"POKER_TRACKER_S3_URI가 없습니다: {args.env_file}")
        s3, prefix = opened
        objects = list_objects(s3, prefix)
    except SyncError as e:
        sys.exit(str(e))
    if not objects:
        sys.exit("S3에 솔루션 DB가 없습니다.")
    items = fetch_items(s3, sorted(objects), stacks)
    per = {}
    for it in items:
        per[int(it["stack"])] = per.get(int(it["stack"]), 0) + 1
    print(f"변환: {len(items)}장 — " + " · ".join(f"{bb}bb {n}" for bb, n in sorted(per.items())))
    if args.dry_run:
        return
    resp = post(args.port, items)
    if not resp.get("ok"):
        sys.exit(f"교체 실패: {resp.get('error')} {resp.get('errors') or ''}")
    print(f"교체 완료: 기존 {resp['removed']}장 제거 → {resp['n']}장 저장")


if __name__ == "__main__":
    main()
