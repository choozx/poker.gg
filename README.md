# Hand History Converter

CoinPoker 토너먼트 핸드 히스토리(txt)를 읽기 쉽게 변환하고,
핸드별 AI(Claude) 분석 · 통계 대시보드 · **뱅크롤(실제 손익) 관리**까지 하는 로컬 웹앱.

![구성] txt 드롭 → 📌 복기 추천에서 핸드 선별 → 🤖 AI 분석 → 📊 종합 리포트로 패턴 진단

## 요구사항

| 항목 | 내용 |
|---|---|
| Python | **3.8 이상** (외부 패키지 불필요 — 표준 라이브러리만 사용) |
| AI 분석 (선택) | [Claude Code](https://claude.com/claude-code) 설치 + 로그인 (`claude` 명령이 PATH에 있어야 함) |
| 클라우드 동기화 (선택) | GitHub 계정 + Personal Access Token (여러 컴퓨터에서 같은 DB를 쓸 때) |

AI 분석 없이 변환/뷰어만 쓰는 경우 Python만 있으면 된다.

---

## 설치 & 실행 — macOS

```bash
# 1. 코드 받기
git clone <저장소 URL>
cd analyze_hand_history

# 2. 실행 (Python3는 macOS 기본 내장)
python3 gui.py
```

브라우저가 자동으로 열린다 (`http://127.0.0.1:8765`). 종료는 터미널에서 `Ctrl+C`.

**AI 분석을 쓰려면** Claude Code 설치 후 1회 로그인:

```bash
# Homebrew 또는 공식 설치 스크립트
curl -fsSL https://claude.ai/install.sh | bash
claude   # 첫 실행 시 로그인
```

## 설치 & 실행 — Windows

```powershell
# 1. Python 설치 (둘 중 하나)
winget install Python.Python.3.12
# 또는 python.org에서 설치 — 설치 시 "Add python.exe to PATH" 반드시 체크

# 2. 코드 받기
git clone <저장소 URL>
cd analyze_hand_history

# 3. 실행 (윈도우는 python3가 아니라 python)
python gui.py
```

**AI 분석을 쓰려면** PowerShell에서 Claude Code 설치 후 1회 로그인:

```powershell
irm https://claude.ai/install.ps1 | iex
claude   # 첫 실행 시 로그인
```

> 설치 후 `claude` 명령이 안 잡히면 터미널을 새로 열거나 PATH를 확인할 것.
> `gui.py` 시작 로그에 `AI 분석 백엔드: claude-cli`가 나오면 정상.

---

## (선택) 여러 컴퓨터에서 동기화 — GitHub Release

두 대 이상에서 같은 DB를 쓰려면, `hands_db.json`을 **자기 GitHub repo의 Release**에
자동으로 올리고/받는 클라우드 동기화를 켤 수 있다. 켜면 더 이상 파일을 수동 복사할 필요가 없다
(끄면 그냥 로컬 파일 모드로 동작 — 동기화는 **선택**이다).

**1) GitHub PAT 발급** — [Fine-grained token](https://github.com/settings/personal-access-tokens/new)
- Resource owner: 본인 계정 / Repository access: **자기 repo만** 선택
- Permissions → Repository permissions → **Contents: Read and write**

**2) 설정 파일 생성** — 작업폴더에 `.ahh_sync.env` (이미 `.gitignore` 처리되어 커밋되지 않음):

```bash
AHH_GH_TOKEN=github_pat_발급한토큰
AHH_GH_REPO=your-id/your-repo
```

**3) 그냥 실행** — `python3 gui.py`. 시작할 때 클라우드에서 받아오고, 끌 때(`Ctrl+C`)
변경분을 자동으로 올린다. (저장은 디바운스로 묶여 올라가고, 내용이 같으면 업로드를 건너뛴다.)

동작 메모:
- 토큰 + repo가 **둘 다** 설정돼야 동기화가 켜진다(opt-in). 하나라도 없으면 로컬 `hands_db.json` 모드.
- 동기화 모드에선 DB가 `~/.cache/analyze_hand_history/`에 캐시되어 **작업폴더는 깨끗**하다.
- DB는 `db-store`(draft) 릴리스에 gzip(~16MB) asset으로 저장되며, 과거 3개 버전까지 보관(롤백용). Release asset이라 **저장·대역폭 과금 없음**.
- ⚠️ **번갈아(껐다 켜며) 쓸 것** — 두 대를 동시에 켜두면 나중에 끈 쪽이 상대 변경을 덮어쓴다.

수동 명령 (직접 받고/올리고/확인):

```bash
python3 cloud_sync.py status               # 토큰·repo·최신 asset 상태
python3 cloud_sync.py pull hands_db.json    # 클라우드 → 로컬 파일
python3 cloud_sync.py push hands_db.json    # 로컬 파일 → 클라우드
```

---

## 주요 기능

- **🔍 토너먼트 검색** — 토너 이름/#번호로 검색 + 정렬(최신·이름·핸드 수) + 페이지네이션(20개씩).
  카드 클릭으로 해당 토너 핸드 목록을 엶 (토너가 수백 개여도 사이드바가 가벼움)
- **토너먼트별 핸드 뷰어** — 포지션/카드(무늬 색상)/블라인드/핸드별 손익 표시.
  핸드가 수만 개여도 가벼움 (토너먼트 목록만 먼저 받고 핸드는 선택 시 로드)
- **AI 분석** — 핸드별 버튼 클릭으로 스트리트별 플레이 분석 (스트리밍 표시).
  분석된 핸드는 목록에 🤖 배지 + 총평 등급 이모지(✅좋음 🙂무난 🤔의문 ❌실수) 표시
- **📈 통계 대시보드** — `요약` 탭: VPIP/PFR, WTSD/W$SD, **포지션별 칩 EV(bb)**.
  `핸드 그리드` 탭: **스타팅 핸드 13×13 매트릭스** — 169개 조합별 히트맵.
  표시 기준 토글: **액션 / RFI(오픈) / 칩 EV** + **포지션 필터**(BTN/UTG…) + **스택 필터**(<15/15-25/25-40/40+bb).
  **액션**은 각 칸을 프리플랍 첫 액션(오픈/3벳/콜/올인/폴드) 스택바로 채워 레인지 믹스를 보여줌
  (바 길이=VPIP, 색이 액션 분해 — VPIP·PFR을 포함하는 상위 뷰).
  RFI는 폴드로 히어로까지 온 경우(오픈 기회) 대비 첫 레이즈 비율 — 솔버 오픈 차트와 같은 정의.
  토큰 소모 없음 (메타 집계).
  칩 EV는 플레이 품질 지표 — 토너 칩은 상금이 아니므로 손익 합산은 제공하지 않음.
  **각 칸 클릭 → 해당 조합 핸드 목록**(현재 포지션·스택 필터 적용)으로 드릴다운
- **💰 뱅크롤** — **실제 돈($)** 손익 관리 (칩 EV와 별개 도메인 — 칩≠상금이라 분리).
  순손익·ROI·ITM·평균 바이인·**누적 손익 그래프**, 토너별 바이인/상금/손익 표.
  **세틀↔본선을 캠페인 트리**로 묶음 — 세틀 핸드로 **이김(시트)/버스트** 자동 판정,
  이긴 세틀만 본선 밑 자식(접기/펼치기), 다단계 스텝 체인 지원. 토너 행 클릭 → 그 토너 핸드.
  **새 핸드 임포트 시 뱅크롤에 자동 추가**(현금 본게임=바이인, 티켓입장=0, **상금은 수동 입력**).
  최초 데이터는 구글시트에서 1회 이주(`bankroll.py`)로 seed
- **📌 복기 추천** — 큰 손실(-10bb↑)/쇼다운 패배/올인 패배 핸드를 자동 선별해서
  전체 토너에서 모아 보여줌 (휴리스틱 — 토큰 소모 없음)
- **📊 종합 리포트** — 분석된 핸드들을 모아 반복되는 실수 패턴을 진단.
  핸드 번호 근거 인용, 우선 교정 1순위 제시. 결과는 DB에 저장
- **핸드 DB** — 변환 결과와 AI 분석을 `hands_db.json`에 영구 저장.
  같은 핸드는 재업로드해도 핸드 번호 기준으로 자동 스킵 (기간 겹쳐도 안전)
- **프리폴드 숨기기** — 앤티/블라인드만 내고 폴드한 핸드 필터
- **마크다운 복사/다운로드** — AI에게 붙여넣기 좋은 형태로 내보내기

## 예정 기능 (로드맵)

- **토너 성적 기반 바이인 추천** — 뱅크롤 성적(ROI·ITM·순손익·변동성)을 바탕으로
  적정 바이인대를 추천 (뱅크롤 관리 관점에서 "지금 칠 만한 금액").
- **토너먼트별 스택 변화 추이** — 한 토너먼트 안에서 히어로 스택을 **절대 칩량**으로
  핸드별로 어떻게 변했는지 그래프로 시각화 (어디서 칩을 잃고/땄는지 한눈에).

---

## 사용법

1. `python3 gui.py` (윈도우: `python gui.py`) 실행
2. 핸드 히스토리 **txt 파일을 화면에 드래그&드롭** (여러 개 가능)
   - 새 핸드만 DB에 추가되고 기존 핸드는 스킵됨 (토스트로 결과 표시)
3. 사이드바 메뉴(**📈 통계 / 💰 뱅크롤 / 📌 복기 추천 / 🔍 토너먼트**)로 이동
   - **🔍 토너먼트** — 이름/#번호 검색·정렬·페이지로 토너 찾아 열기 → 핸드 클릭으로 상세 보기
   - **💰 뱅크롤** — 실제 돈 손익·ROI, 세틀↔본선 캠페인 트리. ➕로 토너 결과 입력(상금 등)
   - **📌 복기 추천** — 큰 손실/쇼다운·올인 패배 핸드만 전체 토너에서 모아 보기
   - 시작 화면은 **📈 통계 대시보드** (전체 성과 요약)
4. 핸드 상세 하단 **🤖 AI 분석** 버튼 → 스트리트별 분석 (결과는 DB에 저장되어 재분석 불필요)
   - 분석된 핸드는 목록에 🤖 + 등급 이모지가 붙어서 실수 핸드(❌)를 한눈에 찾을 수 있음
5. 분석이 3개 이상 쌓이면 헤더의 **📊 종합 리포트** → 반복 실수 패턴 진단

다음 실행부터는 파일 없이 `gui.py`만 실행해도 DB의 핸드가 바로 표시된다.

### 추천 워크플로

```
토너 종료 → txt 드롭 → 📌 복기 추천에서 의심 핸드 AI 분석
→ 분석이 쌓이면 📊 종합 리포트 재생성 → 패턴 교정
```

### 실행 옵션

```bash
python3 gui.py hands.txt            # 시작하면서 txt 임포트
python3 gui.py --port 9000          # 포트 변경 (기본 8765)
python3 gui.py --no-browser         # 브라우저 자동 오픈 끄기
python3 gui.py --hero 닉네임         # 히어로 이름이 'Hero'가 아닐 때
python3 gui.py --db mydb.json       # DB 파일 경로 변경 (기본 hands_db.json, 동기화 모드에선 캐시 경로 사용)
python3 gui.py --rebuild            # 저장된 원본으로 전체 재변환 (AI 분석은 유지)
                                    #   ※ PFR·핸드그리드 RFI/액션/스택필터는 이 옵션으로 1회 재변환해야 표시됨
                                    #     (기존 DB는 해당 필드가 없어 "—"/빈칸으로 나옴)
python3 gui.py --ai cli|api|auto    # AI 백엔드 선택 (아래 참고)
```

### CLI 변환기 (GUI 없이)

```bash
python3 convert.py hands.txt                    # 인터랙티브: 토너 선택 → 변환
python3 convert.py hands.txt --list             # 토너먼트 목록만
python3 convert.py hands.txt --tournament 63446 -o out.md
python3 convert.py hands.txt --format json      # 구조화 데이터로 출력
```

---

## AI 분석 백엔드

| 백엔드 | 조건 | 비용 |
|---|---|---|
| `claude-cli` (기본) | Claude Code 설치 + 로그인 | 구독에 포함 |
| `anthropic-api` | `pip install anthropic` + 환경변수 `ANTHROPIC_API_KEY` | 종량제 과금 |

`--ai auto`(기본)는 API 키가 있으면 API, 없으면 CLI를 자동 선택한다.
분석 모델은 `claude-cli`의 경우 Claude Code의 기본 모델을 따라간다 (`claude /model`로 변경 가능).

프롬프트는 `gui.py`에서 수정할 수 있다 — 핸드 분석은 `ANALYSIS_SYSTEM_PROMPT`,
종합 리포트는 `REPORT_SYSTEM_PROMPT`.

---

## 여러 컴퓨터에서 데이터 공유하기

**권장 — 클라우드 동기화** ([위 "여러 컴퓨터에서 동기화"](#선택-여러-컴퓨터에서-동기화--github-release) 참고):
`.ahh_sync.env`만 설정하면 양쪽에서 `python3 gui.py`로 자동 동기화된다. 파일 복사 불필요.

**동기화를 안 쓸 때 — 수동 복사**: `hands_db.json`은 개인 데이터라 **git에 포함되지 않는다**(`.gitignore`).
이 파일 하나에 모든 핸드(원본 포함)·AI 분석·**뱅크롤(토너 결과·바이인/상금·강제링크)**이 다 들어 있으니,
새 컴퓨터의 같은 폴더에 복사하면 된다. 원본 txt는 옮길 필요 없다 — DB에 raw가 있어 `--rebuild`도 가능하다.

> ⚠️ 뱅크롤은 이미 이 파일에 있으니 **시트 재이주(`python3 bankroll.py`)는 다시 돌리지 말 것** — 수동 입력/상금을 덮어쓴다.

## 파일 구성

| 파일 | 역할 |
|---|---|
| `gui.py` | 웹앱 서버 + 프론트엔드(메인 진입점) |
| `convert.py` | 핸드 히스토리 파서/변환기 (CLI 겸용) |
| `store.py` | 핸드 DB(`hands_db.json`) 로드/저장/병합·집계 |
| `bankroll.py` | 뱅크롤(실제 돈) — 시트 이주·핸드 매칭·캠페인 트리·win/loss 판정 |
| `cloud_sync.py` | (선택) GitHub Release로 DB 클라우드 동기화 (표준 라이브러리만) |
| `.ahh_sync.env` | (선택, git 제외) 동기화용 토큰/repo 설정 |
| `sample_hand.txt` | 테스트용 샘플 핸드 |
| `hands_db.json` | (자동 생성) 핸드·AI분석·뱅크롤 저장소. 동기화 모드에선 `~/.cache/analyze_hand_history/`에 캐시 |

## 트러블슈팅

- **`AI 분석 백엔드: 없음`** — `claude` 명령이 PATH에 없음. Claude Code 설치/로그인 확인.
- **포트 충돌 (`Address already in use`)** — `--port 9000` 등으로 변경하거나 기존 프로세스 종료.
- **윈도우에서 한글 깨짐** — 구형 `cmd.exe` 대신 Windows Terminal 또는 PowerShell 사용 권장.
- **핸드가 안 올라감** — 로그가 `CoinPoker Hand #`로 시작하는 원본 텍스트인지 확인.
- **클라우드 동기화가 안 켜짐** — `python3 cloud_sync.py status`로 확인. 토큰+repo 둘 다 설정됐는지, PAT에 Contents 권한이 있는지 점검.
