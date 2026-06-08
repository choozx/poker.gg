# Hand History Converter

CoinPoker 토너먼트 핸드 히스토리(txt)를 읽기 쉬운 포맷으로 변환하고,
핸드별로 AI(Claude) 분석을 받을 수 있는 로컬 웹앱.

![구성] txt 드롭 → 📌 복기 추천에서 핸드 선별 → 🤖 AI 분석 → 📊 종합 리포트로 패턴 진단

## 주요 기능

- **🔍 토너먼트 검색** — 토너 이름/#번호로 검색 + 정렬(최신·이름·핸드 수) + 페이지네이션(20개씩).
  카드 클릭으로 해당 토너 핸드 목록을 엶 (토너가 수백 개여도 사이드바가 가벼움)
- **토너먼트별 핸드 뷰어** — 포지션/카드(무늬 색상)/블라인드/핸드별 손익 표시.
  핸드가 수만 개여도 가벼움 (토너먼트 목록만 먼저 받고 핸드는 선택 시 로드)
- **AI 분석** — 핸드별 버튼 클릭으로 스트리트별 플레이 분석 (스트리밍 표시).
  분석된 핸드는 목록에 🤖 배지 + 총평 등급 이모지(✅좋음 🙂무난 🤔의문 ❌실수) 표시
- **📈 통계 대시보드** — 전체 핸드를 집계: VPIP/PFR, WTSD/W$SD, **포지션별 칩 EV(bb)**.
  토큰 소모 없음 (메타 집계). 칩 EV는 플레이 품질 지표 — 토너 칩은 상금이 아니므로 손익 합산은 제공하지 않음
- **📌 복기 추천** — 큰 손실(-10bb↑)/쇼다운 패배/올인 패배 핸드를 자동 선별해서
  전체 토너에서 모아 보여줌 (휴리스틱 — 토큰 소모 없음)
- **📊 종합 리포트** — 분석된 핸드들을 모아 반복되는 실수 패턴을 진단.
  핸드 번호 근거 인용, 우선 교정 1순위 제시. 결과는 DB에 저장
- **핸드 DB** — 변환 결과와 AI 분석을 `hands_db.json`에 영구 저장.
  같은 핸드는 재업로드해도 핸드 번호 기준으로 자동 스킵 (기간 겹쳐도 안전)
- **프리폴드 숨기기** — 앤티/블라인드만 내고 폴드한 핸드 필터
- **마크다운 복사/다운로드** — AI에게 붙여넣기 좋은 형태로 내보내기

## 요구사항

| 항목 | 내용 |
|---|---|
| Python | **3.8 이상** (외부 패키지 불필요 — 표준 라이브러리만 사용) |
| AI 분석 (선택) | [Claude Code](https://claude.com/claude-code) 설치 + 로그인 (`claude` 명령이 PATH에 있어야 함) |

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

## 사용법

1. `python3 gui.py` (윈도우: `python gui.py`) 실행
2. 핸드 히스토리 **txt 파일을 화면에 드래그&드롭** (여러 개 가능)
   - 새 핸드만 DB에 추가되고 기존 핸드는 스킵됨 (토스트로 결과 표시)
3. 사이드바 메뉴(**📈 통계 / 📌 복기 추천 / 🔍 토너먼트**)로 이동
   - **🔍 토너먼트** — 이름/#번호 검색·정렬·페이지로 토너 찾아 열기 → 핸드 클릭으로 상세 보기
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
python3 gui.py --db mydb.json       # DB 파일 경로 변경 (기본 hands_db.json)
python3 gui.py --rebuild            # 저장된 원본으로 전체 재변환 (AI 분석은 유지)
                                    #   ※ 통계의 PFR은 이 옵션으로 1회 재변환해야 표시됨
                                    #     (기존 DB는 PFR 필드가 없어 "—"로 나옴)
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

## 다른 컴퓨터로 데이터 옮기기

`hands_db.json`은 개인 데이터라 **git에 포함되지 않는다** (`.gitignore`).
변환 결과와 AI 분석을 함께 옮기려면 이 파일을 직접 복사할 것:

```
analyze_hand_history/
└── hands_db.json    ← 이 파일을 새 컴퓨터의 같은 폴더에 복사
```

이 파일 하나에 모든 핸드(원본 포함)와 AI 분석이 들어 있다.
원본 txt 파일은 옮길 필요 없다 — DB에 raw가 저장되어 있어 `--rebuild`도 가능하다.

## 파일 구성

| 파일 | 역할 |
|---|---|
| `gui.py` | 웹앱 서버 (메인 진입점) |
| `convert.py` | 핸드 히스토리 파서/변환기 (CLI 겸용) |
| `store.py` | 핸드 DB(`hands_db.json`) 로드/저장/병합 |
| `sample_hand.txt` | 테스트용 샘플 핸드 |
| `hands_db.json` | (자동 생성) 핸드 저장소 — 백업 권장 |

## 트러블슈팅

- **`AI 분석 백엔드: 없음`** — `claude` 명령이 PATH에 없음. Claude Code 설치/로그인 확인.
- **포트 충돌 (`Address already in use`)** — `--port 9000` 등으로 변경하거나 기존 프로세스 종료.
- **윈도우에서 한글 깨짐** — 구형 `cmd.exe` 대신 Windows Terminal 또는 PowerShell 사용 권장.
- **핸드가 안 올라감** — 로그가 `CoinPoker Hand #`로 시작하는 원본 텍스트인지 확인.
