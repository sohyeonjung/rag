# FinScope - 기업 비교·리서치 플래너

기업 후보와 재무정보, 사업 배경을 조회해 사용자의 조건에 맞는 기업을 찾고 조사 일정을 저장하는 서비스입니다. 기존 2회차 `finrag`의 기업 목록·법인코드·위키백과 수집·Chroma 검색 구조를 이어갑니다.

> 현재 상태: 2026-11-19 실제 외부 API 3종·GMS 분석/계획·기업 조회·후속 조건 유지·재계획 상한 검증을 통과했습니다.  기존 Chroma 색인 검색은 미검증입니다. 데모는 별도 가상 데이터입니다.

## 빠른 실행 (Windows PowerShell)

저장소 루트에서 실행합니다. Python 3.10+가 필요합니다.

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-planner.txt
Copy-Item .env.example .env  # .env가 없을 때만 실행
.venv/Scripts/python -m uvicorn planner.api:app --host 127.0.0.1 --port 8000
```

두 번째 터미널:

```powershell
.venv/Scripts/python -m http.server 5500 --bind 127.0.0.1 --directory frontend
```

- 화면: http://127.0.0.1:5500
- Swagger API 명세: http://127.0.0.1:8000/docs
- 기본 모드는 **데모**입니다. 가상 기업 12개와 가상 재무 수치를 사용하며 LLM 호출 비용이 없습니다.
- 실제 모드는 `.env`를 채우고 백엔드를 재시작한 후 화면의 데이터 모드에서 선택합니다.
- 직접 테스트할 때는 **실제 API 연결** 모드에서 `삼성전자 기업 1개를 조사해줘`를 입력하고, 이어서 `같은 조건에서 2024년 자료로 바꿔줘`로 대화 맥락을 확인할 수 있습니다. 결과 카드의 조사 계획 저장에서 날짜와 시간을 지정합니다.
- 실행 로그·계획·대화 조건은 `data/planner.sqlite`에 저장됩니다. 사용자 인증 없는 단일 사용자 로컬 실습용이며 외부 공개 배포용 구성이 아닙니다.

## API 키 설정

| 변수 | 용도 | 준비 |
|---|---|---|
| `GMS_KEY` | 요청 분석·작업 계획 LLM | SSAFY GMS 키. 2일차 예제의 `GMS_API_KEY`도 지원 |
| `DART_API_KEY` | 사업보고서 재무 수치 | OpenDART 인증키 신청 |
| `DATA_GO_KR_KEY` | 기업 후보 시세 조회 | 공공데이터포털 금융위원회_주식시세정보 활용 신청 후 서비스키 입력 |
| `WIKI_CONTACT` | 위키백과 User-Agent 연락처 | 선택, 영문 연락처 |
| `PLANNER_MODEL` | 사용할 모델 | 기본 `gpt-4.1-mini`, 계정에서 사용 가능한 모델 확인 |
| `PLANNER_BASE_URL` | LLM 접속 주소 | 기본 SSAFY GMS OpenAI 호환 주소 |

일반 OpenAI 호환 연결을 사용할 경우 `OPENAI_API_KEY`와 `PLANNER_BASE_URL`을 설정합니다. 키는 소스·스크린샷·ZIP에 포함하지 않습니다. 누락·실패는 안전한 오류 코드로 표시합니다.

## 대표 시연

1. `전체 기업 3개` - 한 번에 후보 확보.
2. 새 대화에서 `매출이 10% 이상 성장한 흑자 기업 3개를 찾아줘` - 제외 사유와 재계획 확인.
3. `같은 조건으로 1개만` - 성장·흑자 조건 유지, 개수만 변경.
4. `같은 조건으로 다른 기업 1개` - 직전 추천 기업 제외.
5. 새 대화에서 `매출 999% 이상 성장 기업 1개` - 재계획 3회 상한 확인.
6. `안녕` - Tool 없이 응답.
7. 결과의 **조사 계획 저장**에서 날짜·시작·종료 시각을 입력하고 저장.
8. 겹치는 시간으로 두 번째 계획을 저장해 경고 확인. 조사 일정 화면에서 수정·삭제.

데모 규칙 파서는 제한된 한국어 예시만 지원합니다. 자유로운 날짜·조건 해석은 실제 LLM 모드에서 검증합니다. 일정은 저장 폼에서 명시적으로 지정할 수 있습니다.

## 구현 범위

- 필수 F3001~F3019: Tool 스키마, LangGraph 분석/계획/실행/판단/응답, 상한 있는 재계획, 세션 메모리, 이벤트·로그, 계획 CRUD, 분리된 FastAPI/HTML UI.
- F3022: 기존 Chroma 문서 검색 어댑터 구현. 색인 미연결 시 `NO_INDEX`. 실제 색인 검색은 미검증.
- F3020/F3021/F3023: 미선택. 로그 이상 진단, Planner/Worker 분리, Google Calendar는 구현하지 않습니다.
- 입력 조건: 사업 키워드·기업 수(1~10)·사업연도·매출 성장률 하한·영업흑자 여부. 지원하지 않는 재무 제약은 되묻습니다.
- 재무 판단은 코드로 수행합니다. 전기 매출이 0 이하이면 성장률을 만들지 않습니다. 연결/별도 재무제표를 섞지 않습니다.
- 기본 사업연도는 전년도, 기본 기업 수는 3개, 시세 기준일은 어제입니다. 응답에 적용된 기준을 표시합니다.
- 후보는 기존 기업 목록에 포함되어 있고 조회 기준일에 확인된 기업으로 한정됩니다. 모든 상장기업을 대상으로 한 순위나 투자 추천이 아닙니다.
- 소스 자료에서 확인할 수 없는 값, 미래 시세, 분석 소요시간을 추측해 채우지 않습니다. 조사 시간은 사용자가 지정합니다.

## 구조

```text
frontend/          대화·기업 카드·조사 일정·실행 로그 화면
planner/api.py     FastAPI 엔드포인트와 검증
planner/agent.py   LangGraph 및 대화 메모리 적용
planner/tools.py   외부 API 3종 + 기존 RAG Tool
planner/events.py  이벤트 버스·로깅 구독자 (LLM 사용 안 함)
planner/storage.py SQLite 계획·세션·로그
planner/fixtures.py 명시적인 가상 데이터
finrag/            기존 2회차 수집·검색 코드
```

## 테스트

```powershell
.venv/Scripts/python -m unittest discover -s tests
.venv/Scripts/python scripts/check_planner_live.py
```

자동 테스트 42개와 실제 API 3종·GMS 분석/계획·후속 연도 변경·재계획 3회 상한 검증을 통과했습니다. 실제 응답은 로컬에서만 보관합니다. 기존 Chroma 색인 연결 후 정상 검색은 미검증입니다.

공개자료 기반 학습·조사 도구이며 투자 권유나 전문 투자자문을 대신하지 않습니다.
