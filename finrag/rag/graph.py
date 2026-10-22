"""RAG 파이프라인: LangGraph retrieve → generate 두 노드. (F2006~F2008)

retrieve  선택 기업의 메타 청크(검색 없이 항상) + 외부 문서 청크(그 기업으로 범위를 좁혀 Top-k)
          → 병합·중복 제거 → [근거 1..N] 번호 고정
generate  P3로 근거만 사용해 답변 → 인용된 근거만 골라 돌려준다
"""

from functools import lru_cache
from typing import TypedDict

from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import END, START, StateGraph

from finrag.config import settings
from finrag.domain import prompts
from finrag.llm import chat
from finrag.rag import glossary_lookup
from finrag.rag.citations import SOURCE_LABELS, cited_sources
from finrag.store.vectorstore import collection


class RAGState(TypedDict, total=False):
    stock_code: str
    company: str
    question: str
    task: str                # qa | briefing | factcheck | term
    search_type: str         # similarity | mmr (설정값을 화면에서 바꿔 비교할 때)
    top_k: int
    decompose: bool          # 고도화: 질문 분해 검색을 켤지 (기본은 config.yaml)
    contexts: list[dict]     # [{n, id, source_type, title, url, doc_date, text}]
    answer: str
    cited: list[dict]


def _to_context(doc_id: str, text: str, meta: dict) -> dict:
    return {
        "id": doc_id,
        "source_type": meta.get("source_type", "wiki"),
        "title": meta.get("title", ""),
        "url": meta.get("source_url", ""),
        "doc_date": meta.get("doc_date", ""),
        "text": text,
    }


def split_question(company: str, question: str) -> list[str]:
    """두 가지 이상을 묻는 질문을 하위 질문으로 나눈다. 실패하거나 하나뿐이면 [원문]."""
    prompt = ChatPromptTemplate.from_messages([("system", prompts.DECOMPOSE_SYSTEM), ("human", prompts.DECOMPOSE_HUMAN)])
    try:
        out = (prompt | chat("query").with_structured_output(prompts.SubQuestions)).invoke(
            {"company": company, "question": question})
        subs = [q.strip() for q in out.questions if q.strip()][:3]
    except Exception:
        subs = []
    return subs if len(subs) > 1 else [question]


def retrieve(state: RAGState) -> dict:
    conf = settings()["retrieval"]
    code = state["stock_code"]
    k = state.get("top_k") or conf["top_k"]
    search_type = state.get("search_type") or conf["search_type"]

    contexts: list[dict] = []
    # ① 선택 기업 메타 청크 — 검색 없이 ID로 가져와 항상 포함
    got = collection("meta").get(ids=[code], include=["documents", "metadatas"])
    for doc_id, text, meta in zip(got["ids"], got["documents"], got["metadatas"]):
        contexts.append(_to_context(doc_id, text, meta))

    # ② 외부 문서 — 그 기업 문서로만 범위를 좁힌다 (filter 누락 시 다른 기업 문서가 섞인다)
    docs_store = collection("docs")
    # "이 회사", "이 기업"처럼 이름 없는 질문도 그 기업 내용으로 검색되도록 기업명을 붙인다
    query = f"{state.get('company', '')} {state['question']}".strip()
    flt = {"stock_code": code}
    found = []
    # 기업 본문서 청크를 먼저 확보한다. '반도체'처럼 긴 일반 문서가 Top-k를 독차지하는 것을 막는다. (트러블슈팅 #4)
    wiki_title = (got["metadatas"][0] if got["metadatas"] else {}).get("wiki_title")
    if wiki_title and conf.get("own_doc_k"):
        own_flt = {"$and": [{"stock_code": code}, {"title": wiki_title}]}
        found += docs_store.similarity_search(query, k=conf["own_doc_k"], filter=own_flt)

    def search(q: str, n: int) -> list:
        if search_type == "mmr":
            return docs_store.max_marginal_relevance_search(
                q, k=n, fetch_k=conf["fetch_k"], lambda_mult=conf["mmr_lambda"], filter=flt)
        return docs_store.similarity_search(q, k=n, filter=flt)

    # 고도화: 두 가지 이상을 묻는 질문은 하위 질문마다 따로 검색해 번갈아 담는다.
    # 한 번에 검색하면 한쪽 사실의 청크만 Top-k를 채워 다른 쪽 근거가 빠졌다 (골든셋 multi chunk_recall 0.70)
    decompose = state.get("decompose", conf.get("decompose", False)) and state.get("task", "qa") in ("qa", "factcheck")
    subs = split_question(state.get("company", ""), state["question"]) if decompose else []
    if len(subs) > 1:
        per_query = [search(f"{state.get('company', '')} {s}".strip(), k) for s in subs]
        for rank in range(k):  # 라운드 로빈: 하위 질문별 1위, 2위, ... 순서로
            found += [hits[rank] for hits in per_query if rank < len(hits)]
    else:
        found += search(query, k)

    # ③ 병합 · 중복 제거 (청크 ID 기준). 질문 분해를 했으면 외부 문서 수를 본문서 + k로 맞춘다
    seen = {c["id"] for c in contexts}
    limit = len(contexts) + (conf.get("own_doc_k") or 0) + k
    for doc in found:
        if len(contexts) >= limit:
            break
        m = doc.metadata
        doc_id = f"{m.get('stock_code')}:{m.get('source_type')}:{m.get('doc_id')}:{m.get('seq')}"
        if doc_id in seen:
            continue
        seen.add(doc_id)
        contexts.append(_to_context(doc_id, doc.page_content, m))

    # ④ 한국은행 용어집 — 기업과 무관한 공용 근거라 기업 필터 없이, 질문에 나온 용어 위주로 (F2016)
    term_task = state.get("task") == "term"
    for doc_id, text, meta in glossary_lookup.lookup(
        state["question"], k=3 if term_task else conf["glossary_k"], min_score=0.35 if term_task else None
    ):
        if doc_id not in seen:
            seen.add(doc_id)
            contexts.append(_to_context(doc_id, text, meta))

    # ⑤ 번호 고정 — 이 번호가 답변 본문의 [근거 N]과 화면 목록을 잇는 유일한 기준
    for n, c in enumerate(contexts, start=1):
        c["n"] = n
    return {"contexts": contexts}


def format_context(contexts: list[dict]) -> str:
    blocks = []
    for c in contexts:
        label = SOURCE_LABELS.get(c["source_type"], c["source_type"])
        date_note = f" · 기준일 {c['doc_date']}" if c.get("doc_date") else ""
        blocks.append(f"[근거 {c['n']}] ({label}) {c['title']}{date_note}\n{c['text']}")
    return "\n\n".join(blocks)


def generate(state: RAGState) -> dict:
    contexts = state.get("contexts", [])
    prompt = ChatPromptTemplate.from_messages([("system", prompts.P3_SYSTEM), ("human", prompts.P3_HUMAN)])
    answer = (prompt | chat("answer")).invoke({
        "not_found": prompts.NOT_FOUND,
        "task_rule": prompts.TASK_RULES[state.get("task", "qa")],
        "context": format_context(contexts) or "(근거 없음)",
        "company": state.get("company", ""),
        "question": state["question"],
    }).content
    return {"answer": answer, "cited": cited_sources(answer, contexts)}


@lru_cache
def build_graph():
    g = StateGraph(RAGState)
    g.add_node("retrieve", retrieve)
    g.add_node("generate", generate)
    g.add_edge(START, "retrieve")
    g.add_edge("retrieve", "generate")
    g.add_edge("generate", END)
    return g.compile()


def ask(stock_code: str, company: str, question: str, task: str = "qa", **overrides) -> RAGState:
    return build_graph().invoke(
        {"stock_code": stock_code, "company": company, "question": question, "task": task, **overrides}
    )
