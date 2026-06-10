"""핸드 DB를 GitHub Release에 올리고/받는 클라우드 동기화 (표준 라이브러리만).

왜 GitHub Release인가:
- Release 첨부파일(asset)은 **저장 용량·대역폭 과금이 없다** (파일당 2GiB 미만). Git LFS와 다름.
- 이미 쓰는 repo라 추가 가입이 없다.

동작 모델 (객체 스토리지 = 파일 통째 교체. 행 단위 수정 아님):
- pull(): Release에서 최신 asset 받아 → gunzip → DB(dict).
- push(db): DB → json → gzip → asset 업로드. 이전 asset은 몇 개만 남기고 정리(롤백 여유).
- asset 이름에 타임스탬프를 박아 **업로드를 먼저 하고 옛 것을 지운다** → "빈 상태" 구간이 없다.

멀티유저: 동기화는 **opt-in**이다. 토큰+리포 둘 다 설정해야 켜진다(available()).
둘 중 하나라도 없으면 앱은 로컬 hands_db.json 모드로 동작 → 누가 clone/fork 하든
아무 설정 없이 바로 쓸 수 있고, 원하는 사람만 자기 저장소를 붙인다. 내 repo를
하드코딩하지 않으므로(기본값 없음) 남이 실수로 내 저장소를 가리킬 일이 없다.

설정 — 환경변수 또는 설정파일 (환경변수 우선). 둘 다 git 추적 안 됨:
- AHH_GH_TOKEN : GitHub PAT. 권한 = 자기 repo Contents read/write. (없으면 로컬 모드)
- AHH_GH_REPO  : 자기 "owner/repo". 기본값 없음 — 반드시 직접 지정. (없으면 로컬 모드)
- AHH_GH_TAG   : Release 태그 (기본: db-store, draft 릴리스라 목록에 안 보임)

설정파일 방식(zshrc 안 건드림): 아래 중 한 곳에 KEY=VALUE 로 작성.
- 작업폴더 `.ahh_sync.env`  (.gitignore 처리됨, 가장 간단)
- `~/.config/analyze_hand_history/sync.env`
- 또는 $AHH_CONFIG 로 경로 지정
  예)
      AHH_GH_TOKEN=github_pat_xxxxx
      AHH_GH_REPO=choozx/analyze-hand-history

CLI:
    python3 cloud_sync.py status              # 토큰/리포/최신 asset 상태
    python3 cloud_sync.py pull <out.json>     # 클라우드 → 로컬 파일
    python3 cloud_sync.py push <in.json>      # 로컬 파일 → 클라우드 (최초 seed 포함)
"""

import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.request

DEFAULT_TAG = "db-store"
ASSET_PREFIX = "hands_db-"
ASSET_SUFFIX = ".json.gz"
KEEP_ASSETS = 3                       # 롤백용으로 남길 과거 버전 수
API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"


# 설정은 환경변수 또는 설정파일에서 읽는다 (환경변수 우선).
# 파일 형식 = KEY=VALUE 한 줄씩 (zshrc의 `export KEY=...` 줄을 그대로 붙여넣어도 됨).
# 탐색 순서: $AHH_CONFIG → 작업폴더 .ahh_sync.env → ~/.config/analyze_hand_history/sync.env
_CONFIG_CACHE = None


def _config_paths():
    paths = []
    if os.environ.get("AHH_CONFIG"):
        paths.append(os.path.expanduser(os.environ["AHH_CONFIG"]))
    here = os.path.dirname(os.path.abspath(__file__))
    paths.append(os.path.join(here, ".ahh_sync.env"))
    paths.append(os.path.expanduser("~/.config/analyze_hand_history/sync.env"))
    return paths


def _parse_env_file(path):
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):                  # zshrc 줄 그대로 허용
                line = line[len("export "):]
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _config():
    global _CONFIG_CACHE
    if _CONFIG_CACHE is None:
        _CONFIG_CACHE = {"_path": ""}
        for p in _config_paths():
            if os.path.exists(p):
                _CONFIG_CACHE = {**_parse_env_file(p), "_path": p}
                break
    return _CONFIG_CACHE


def _get(key):
    return (os.environ.get(key) or _config().get(key, "")).strip()


def config_source():
    """설정을 읽어온 파일 경로 (환경변수만 쓰면 빈 문자열)."""
    return _config().get("_path", "")


def token():
    return _get("AHH_GH_TOKEN")


def repo():
    return _get("AHH_GH_REPO")


def tag():
    return _get("AHH_GH_TAG") or DEFAULT_TAG


def available():
    """토큰 + 리포가 **둘 다** 설정돼야 클라우드 동기화 켜짐 (opt-in). 아니면 로컬 모드."""
    return bool(token() and repo())


def config_hint():
    """설정을 일부만 한 경우 무엇이 빠졌는지 한 줄 안내. 완전/완전미설정이면 빈 문자열."""
    t, r = bool(token()), bool(repo())
    if t and not r:
        return "AHH_GH_TOKEN은 있지만 AHH_GH_REPO가 없습니다 — 자기 repo를 'owner/name' 형식으로 설정하세요."
    if r and not t:
        return "AHH_GH_REPO는 있지만 AHH_GH_TOKEN이 없습니다 — 자기 PAT를 설정하세요."
    return ""


class CloudError(RuntimeError):
    pass


def _headers(extra=None):
    h = {
        "Authorization": f"Bearer {token()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "analyze-hand-history-sync",
    }
    if extra:
        h.update(extra)
    return h


def _api(method, url, data=None, headers=None, timeout=60):
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=headers or _headers())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            return r.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read()


# --- Release / asset 조회 ---------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """asset 다운로드 시 GitHub은 S3로 302 리다이렉트한다.
    Authorization 헤더를 S3로 그대로 보내면 거부되므로, 자동 추적을 막고 수동 처리."""
    def redirect_request(self, *a, **k):
        return None


def _get_release():
    """태그의 Release를 반환(없으면 None). draft 릴리스도 찾도록 목록을 훑는다."""
    st, body = _api("GET", f"{API}/repos/{repo()}/releases/tags/{tag()}")
    if st == 200:
        return json.loads(body)
    if st == 404:
        # draft 릴리스는 tags 엔드포인트에 안 잡힐 수 있어 목록에서 직접 찾는다.
        st2, body2 = _api("GET", f"{API}/repos/{repo()}/releases?per_page=100")
        if st2 == 200:
            for rel in json.loads(body2):
                if rel.get("tag_name") == tag():
                    return rel
        return None
    raise CloudError(f"릴리스 조회 실패 ({st}): {body[:200]!r}")


def _ensure_release():
    rel = _get_release()
    if rel:
        return rel
    payload = json.dumps({
        "tag_name": tag(), "name": "hands_db store",
        "body": "핸드 DB 동기화 저장소 (앱 자동 관리)", "draft": True,
    }).encode()
    st, body = _api("POST", f"{API}/repos/{repo()}/releases", data=payload)
    if st not in (200, 201):
        raise CloudError(f"릴리스 생성 실패 ({st}): {body[:200]!r}")
    return json.loads(body)


def _db_assets(rel):
    """DB asset 목록을 최신순(이름의 타임스탬프 내림차순)으로 반환."""
    assets = [a for a in rel.get("assets", [])
              if a["name"].startswith(ASSET_PREFIX) and a["name"].endswith(ASSET_SUFFIX)]
    def ts(a):
        try:
            return int(a["name"][len(ASSET_PREFIX):-len(ASSET_SUFFIX)])
        except ValueError:
            return 0
    return sorted(assets, key=ts, reverse=True)


# --- pull / push ------------------------------------------------------------

def pull():
    """클라우드 최신 DB를 dict로 반환. asset이 아직 없으면 None."""
    rel = _get_release()
    if not rel:
        return None
    assets = _db_assets(rel)
    if not assets:
        return None
    asset = assets[0]
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(
        f"{API}/repos/{repo()}/releases/assets/{asset['id']}",
        headers=_headers({"Accept": "application/octet-stream"}))
    try:
        with opener.open(req, timeout=120) as r:
            gz = r.read()
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308):
            loc = e.headers["Location"]                 # S3 presigned URL (인증 불필요)
            with urllib.request.urlopen(loc, timeout=120) as r:
                gz = r.read()
        else:
            raise CloudError(f"asset 다운로드 실패 ({e.code})")
    raw = gzip.decompress(gz)
    return json.loads(raw)


def push(db):
    """DB(dict)를 gzip 압축해 새 asset으로 업로드하고, 과거 버전은 KEEP_ASSETS개만 남긴다."""
    rel = _ensure_release()
    raw = json.dumps(db, ensure_ascii=False, indent=1).encode("utf-8")
    gz = gzip.compress(raw, compresslevel=6)
    name = f"{ASSET_PREFIX}{int(time.time())}{ASSET_SUFFIX}"
    url = f"{UPLOADS}/repos/{repo()}/releases/{rel['id']}/assets?name={name}"
    # 업로드 먼저 → 빈 구간 없음
    st, body = _api("POST", url, data=gz,
                    headers=_headers({"Content-Type": "application/octet-stream",
                                      "Content-Length": str(len(gz))}),
                    timeout=180)
    if st not in (200, 201):
        raise CloudError(f"업로드 실패 ({st}): {body[:200]!r}")
    # 정리: 방금 올린 것 포함 최신 KEEP_ASSETS개만 남기고 옛 asset 삭제
    rel = _get_release()
    for old in _db_assets(rel)[KEEP_ASSETS:]:
        _api("DELETE", f"{API}/repos/{repo()}/releases/assets/{old['id']}")
    return len(gz)


# --- CLI --------------------------------------------------------------------

def _cmd_status():
    if not available():
        hint = config_hint()
        print("✗ 클라우드 동기화 꺼짐 — 로컬 hands_db.json 모드")
        print(f"  {hint}" if hint else "  켜려면 AHH_GH_TOKEN + AHH_GH_REPO 둘 다 설정하세요.")
        return 1
    t = token()
    print(f"토큰   : {t[:7]}…{t[-4:]} (설정됨)")
    print(f"리포   : {repo()}")
    print(f"태그   : {tag()}")
    print(f"설정   : {config_source() or '환경변수'}")
    try:
        rel = _get_release()
    except CloudError as e:
        print(f"릴리스 : 조회 실패 — {e}")
        print("  → 토큰이 잘못됐거나(401) repo 권한이 없을 수 있습니다. PAT와 AHH_GH_REPO를 확인하세요.")
        return 1
    if not rel:
        print("릴리스 : 없음 (push 시 자동 생성)")
        return 0
    assets = _db_assets(rel)
    if not assets:
        print("asset  : 없음 (아직 업로드 안 됨)")
    else:
        a = assets[0]
        print(f"asset  : {a['name']}  ({a['size']/1e6:.1f} MB)  업데이트 {a.get('updated_at','')}")
        if len(assets) > 1:
            print(f"         (+ 과거 버전 {len(assets)-1}개 보관)")
    return 0


def _cmd_pull(out):
    db = pull()
    if db is None:
        print("클라우드에 DB가 아직 없습니다.")
        return 1
    with open(out, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=1)
    print(f"✅ 받음 → {out}  (핸드 {len(db.get('hands',{}))}개)")
    return 0


def _cmd_push(src):
    with open(src, encoding="utf-8") as f:
        db = json.load(f)
    n = push(db)
    print(f"✅ 올림 ← {src}  (압축 {n/1e6:.1f} MB, 핸드 {len(db.get('hands',{}))}개)")
    return 0


def main(argv):
    if not argv or argv[0] == "status":
        return _cmd_status()
    cmd = argv[0]
    if not available():
        print("✗ AHH_GH_TOKEN 환경변수를 먼저 설정하세요.")
        return 1
    try:
        if cmd == "pull" and len(argv) == 2:
            return _cmd_pull(argv[1])
        if cmd == "push" and len(argv) == 2:
            return _cmd_push(argv[1])
    except CloudError as e:
        print(f"✗ {e}")
        return 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
