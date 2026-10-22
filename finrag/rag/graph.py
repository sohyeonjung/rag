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
from finrag.rag.citations import SOURCE_LABELS, cited_sources
from finrag.store.vectorstore import collection


class RAGState(TypedDict, total=False):
    stock_code: str
    company: str
    question: str
    task: str                # qa | briefing | factcheck
    search_type: str         # similarity | mmr (설정값을 화면에서 바꿔 비교할 때)
    top_k: int
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
    if search_type == "mmr":
        found += docs_store.max_marginal_relevance_search(
            query, k=k, fetch_k=conf["fetch_k"], lambda_mult=conf["mmr_lambda"], filter=flt)
    else:
        found += docs_store.similarity_search(query, k=k, filter=flt)

    # ③ 병합 · 중복 제거 (청크 ID 기준) · 번호 고정
    seen = {c["id"] for c in contexts}
    for doc in found:
        m = doc.metadata
        doc_id = f"{m.get('stock_code')}:{m.get('source_type')}:{m.get('doc_id')}:{m.get('seq')}"
        if doc_id in seen:
            continue
        seen.add(doc_id)
        contexts.append(_to_context(doc_id, doc.page_content, m))

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
