"""외부 문서 수집·색인 (RAG 2, F2003~F2005).

질문 → P1 검색어 추출 → 캐시에 없는 검색어만 위키 검색 → P2 후보 선별 → 본문 수집 → 청킹 → company_docs 색인
기업 자체 문서(위키 본문서)는 선별 결과와 상관없이 항상 색인한다.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate

from finrag.collect import cache, dart_report, wiki
from finrag.collect.http import ExternalAPIError
from finrag.config import settings
from finrag.domain import prompts
from finrag.domain.companies import build_item_text
from finrag.llm import chat
from finrag.store.chunking import split, split_dart
from finrag.store.vectorstore import collection

Progress = Callable[[str], None]


@dataclass
class CollectReport:
    """화면 진행 상태와 검증(4.2)에 쓰는 수집 결과."""
    queries: list[str] = field(default_factory=list)          # P1이 뽑은 검색어 (+ 기업 본문서)
    skipped_queries: list[str] = field(default_factory=list)  # 캐시라 다시 수집하지 않은 검색어
    candidates: list[dict] = field(default_factory=list)      # P2에 보여 준 후보
    selected: list[str] = field(default_factory=list)         # 색인한 문서 제목
    rejected: list[str] = field(default_factory=list)         # 버린 후보 제목
    chunks: int = 0
    select_reason: str = ""
    dart_report: str = ""                                     # 이번에 색인한 공시 이름
    notes: list[str] = field(default_factory=list)            # 화면에 보여 줄 안내 (실패해도 답변은 계속)


def extract_queries(item: dict, question: str) -> list[str]:
    """P1. 실패해도 서비스가 멈추지 않게 빈 목록을 돌려준다(기업 본문서는 따로 들어간다)."""
    max_q = settings()["collect"]["max_queries"]
    prompt = ChatPromptTemplate.from_messages([("system", prompts.P1_SYSTEM), ("human", prompts.P1_HUMAN)])
    try:
        plan = (prompt | chat("query").with_structured_output(prompts.QueryPlan)).invoke(
            {"max_queries": max_q, "item_text": build_item_text(item), "question": question}
        )
        queries = [q.strip() for q in plan.queries if q.strip()]
    except Exception:
        queries = []
    return list(dict.fromkeys(queries))[:max_q]


def select_candidates(item: dict, question: str, candidates: list[dict]) -> tuple[list[int], str]:
    """P2. 본문을 받기 전에 제목·요약만 보고 고른다. 외부 호출과 임베딩 비용을 줄이기 위해서다."""
    if not candidates:
        return [], ""
    listing = "\n".join(f"{i}. {c['title']} — {c['snippet'][:120]}" for i, c in enumerate(candidates, start=1))
    prompt = ChatPromptTemplate.from_messages([("system", prompts.P2_SYSTEM), ("human", prompts.P2_HUMAN)])
    try:
        result = (prompt | chat("select").with_structured_output(prompts.Selection)).invoke({
            "max_select": settings()["collect"]["max_queries"] + 2,
            "company": item["name"], "question": question, "candidates": listing,
        })
        picked = [i - 1 for i in result.selected if 1 <= i <= len(candidates)]
        return list(dict.fromkeys(picked)), result.reason
    except Exception:
        return [], "선별 실패 — 기업 본문서만 사용"


def index_page(item: dict, page: dict, query: str) -> int:
    """문서 1건을 청킹해 색인한다. ID = 종목코드:출처:문서ID:순번 → 여러 기업이 같은 문서를 모아도 덮어쓰지 않는다."""
    chunks = split(page["text"], "wiki")[: settings()["collect"]["max_chunks_per_doc"]]
    if not chunks:
        return 0
    today = date.today().isoformat()
    meta = {
        "stock_code": item["stock_code"], "company_name": item["name"],
        "source_type": "wiki", "title": page["title"], "source_url": page["url"],
        "doc_id": str(page["page_id"]), "query": query,
        "collected_at": today, "doc_date": today,  # 위키는 수집 시점 스냅샷
    }
    ids = [f"{item['stock_code']}:wiki:{page['page_id']}:{i}" for i in range(len(chunks))]
    docs = [Document(page_content=c, metadata={**meta, "seq": i}) for i, c in enumerate(chunks)]
    collection("docs").add_documents(docs, ids=ids)
    return len(chunks)


def index_dart(item: dict, progress: Progress) -> tuple[str, int]:
    """기업의 최근 정기보고서를 한 번만 색인한다. (보고서 이름, 청크 수). 이미 했으면 ('', 0)."""
    if not item.get("corp_code") or cache.dart_done(item["stock_code"]):
        return "", 0
    progress("DART 공시 확인 중...")
    report = dart_report.latest_report(item["corp_code"])
    if not report:
        return "", 0
    progress(f"공시 수집: {report['report_nm']}")
    d = report["rcept_dt"]
    doc_date = f"{d[:4]}-{d[4:6]}-{d[6:]}"
    url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={report['rcept_no']}"

    docs, ids = [], []
    for sec in dart_report.sections(report["rcept_no"]):
        for chunk in split_dart(sec["text"]):
            seq = len(docs)
            ids.append(f"{item['stock_code']}:dart:{report['rcept_no']}:{seq}")
            docs.append(Document(page_content=f"[{sec['section']}]\n{chunk}", metadata={
                "stock_code": item["stock_code"], "company_name": item["name"], "source_type": "dart",
                "title": f"{report['report_nm']} · {sec['section'].split(' > ')[-1]}", "source_url": url,
                "doc_id": report["rcept_no"], "section": sec["section"], "query": "",
                "collected_at": date.today().isoformat(), "doc_date": doc_date, "seq": seq,
            }))
    limit = settings()["collect"]["max_chunks_per_report"]
    if docs:
        collection("docs").add_documents(docs[:limit], ids=ids[:limit])
    cache.mark_dart(item["stock_code"], report["rcept_no"])
    return report["report_nm"], min(len(docs), limit)


def collect(item: dict, question: str, progress: Progress = lambda _: None) -> CollectReport:
    report = CollectReport()
    done = cache.get(item["stock_code"])
    known_pages = set(done["page_ids"])
    conf = settings()["collect"]

    progress("검색어를 고르는 중...")
    # 기업 자신의 이름은 본문서로 따로 넣으므로 검색어에서 뺀다 (이름 검색은 동음이의어·그룹 문서를 끌어온다)
    own_names = {item["name"], item.get("wiki_title", ""), item.get("corp_name_full", ""), *item.get("aliases", [])}
    queries = [q for q in extract_queries(item, question) if q not in own_names]
    report.queries = ([item["wiki_title"]] if item.get("wiki_title") else []) + queries
    new_queries = [q for q in queries if q not in done["queries"]]
    report.skipped_queries = [q for q in queries if q in done["queries"]]

    pages_to_index: list[tuple[dict, str]] = []

    # ① 기업 본문서: 선별·길이 규칙보다 우선해 항상 색인
    own_title = item.get("wiki_title")
    if own_title and own_title not in done["titles"]:
        progress(f"기업 문서 수집: {own_title}")
        page = wiki.fetch_page(own_title)
        if page:
            pages_to_index.append((page, own_title))

    # ② 새 검색어만 위키 검색 → 후보 모으기
    seen = set(known_pages) | {p["page_id"] for p, _ in pages_to_index}
    for q in new_queries:
        progress(f"위키백과 검색: {q}")
        for hit in wiki.search(q, limit=conf["max_candidates_per_query"]):
            if hit["page_id"] not in seen:
                seen.add(hit["page_id"])
                report.candidates.append({**hit, "query": q})

    # ③ P2 선별 → 고른 문서만 본문 수집
    if report.candidates:
        progress(f"후보 {len(report.candidates)}건 중 관련 문서 고르는 중...")
        picked, report.select_reason = select_candidates(item, question, report.candidates)
        # 제목이 검색어와 정확히 같은 문서는 규칙으로 살린다 (P2가 지나치게 보수적일 때 대비 — 트러블슈팅 #2)
        picked = set(picked) | {i for i, c in enumerate(report.candidates) if c["title"] in queries}
        for i, c in enumerate(report.candidates):
            if i in picked:
                page = wiki.fetch_page(c["title"])
                if page and page["text"]:
                    pages_to_index.append((page, c["query"]))
            else:
                report.rejected.append(c["title"])

    # ④ 청킹·색인
    for page, q in pages_to_index:
        progress(f"색인: {page['title']}")
        report.chunks += index_page(item, page, q)
        report.selected.append(page["title"])

    cache.update(item["stock_code"], new_queries, [p for p, _ in pages_to_index])

    # ⑤ 공시(두 번째 출처 종류). 실패해도 위키 근거로 답변은 계속한다.
    if conf.get("dart"):
        try:
            name, n = index_dart(item, progress)
            if name:
                report.dart_report, report.chunks = name, report.chunks + n
                report.selected.append(name)
        except ExternalAPIError as e:
            report.notes.append(f"공시를 가져오지 못해 위키백과 근거만 사용합니다. ({e})")
    return report
