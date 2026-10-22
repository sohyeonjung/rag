# FinRAG — 근거로 답하는 상장기업 리서치

코스피 시가총액 상위 100개 기업에 대한 질문을 **공개 문서(한국어 위키백과 · DART)** 를 근거로만 답하고,
답변에 실제로 인용한 근거의 제목과 원문 링크를 함께 보여 주는 RAG 서비스입니다.

> ⚠️ 본 서비스는 공개된 문서를 근거로 정보를 제공하며, 투자 권유나 투자 자문이 아닙니다.

## 구조

```
질문 ─▶ P1 검색어 추출 ─▶ 위키백과 검색 (수집 캐시에 없는 검색어만)
     ─▶ P2 후보 선별 (제목·요약만 보고) ─▶ 본문 수집 ─▶ 청킹 ─▶ company_docs 색인
     ─▶ LangGraph [retrieve ─▶ generate]
          retrieve : company_meta(선택 기업, 항상) + company_docs(그 기업 범위 Top-k) 병합·중복 제거
          generate : P3 근거 기반 답변 ─▶ [근거 N] 파싱 ─▶ 인용된 근거만 표시
```

| 폴더 | 책임 |
| --- | --- |
| `finrag/domain/` | 도메인에 묶인 코드 — 항목 텍스트, 별칭, 프롬프트, 면책 문구 |
| `finrag/collect/` | 외부 API (KRX · DART · 위키백과), 수집 캐시, 문서 수집 파이프라인 |
| `finrag/store/` | 청킹, Chroma 컬렉션 |
| `finrag/rag/` | LangGraph 파이프라인, 근거 번호 파싱 |
| `finrag/features/` | 조건 기반 탐색 |
| `app.py` | Streamlit 화면 |
| `scripts/` | 항목 구축 · 색인 · 점검 스크립트 |

## 실행

```bash
python -m venv .venv
source .venv/Scripts/activate          # Windows Git Bash
pip install -r requirements.txt
cp .env.example .env                    # 키 채우기

python scripts/check_gms.py             # GMS 크레딧·채팅·임베딩 점검
python scripts/build_items.py           # 항목 100개 → data/items.json
python scripts/index_items.py           # 기업 메타데이터 색인
streamlit run app.py
```

## 설정

검색·답변 품질에 영향을 주는 값(청크 크기·중복, Top-k, 검색 방식, 모델)은 `config.yaml` 에서 바꿉니다.
`MODEL_PROFILE=dev` 는 모든 단계를 작은 모델로, `final` 은 답변 생성만 상위 모델로 돌립니다.

## 데이터 출처와 이용 조건

| 출처 | 용도 | 이용 조건 |
| --- | --- | --- |
| 공공데이터포털 금융위원회_주식시세정보 / KRX 정보데이터시스템 | 시가총액 상위 종목 선정 (종목코드·종목명·순위만 사용) | 공공누리 4유형 — 원본은 저장소에 포함하지 않음 |
| 금융감독원 OpenDART | 기업개황 (대표자·설립일·결산월 등) | 공공정보, 출처 표기 |
| 한국어 위키백과 | 기업 개요, 질문 관련 문서 | CC BY-SA 4.0, 답변 근거에 원문 링크 표시 |
