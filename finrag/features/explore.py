"""조건 기반 기업 탐색 (F2011): 메타 색인에서 후보를 찾고, LLM이 조건에 맞는 기업만 고른다.

후보 검색은 벡터 유사도 + 키워드 일치를 합친다. 벡터 검색만 쓰면 "반도체 관련 기업"에서
개요에 '메모리 반도체'가 있는 SK하이닉스가 상위 30개에도 들지 못했다. (트러블슈팅 #3)
"""

import re

from langchain_core.prompts import ChatPromptTemplate

from finrag.config import settings
from finrag.domain import prompts
from finrag.llm import chat
from finrag.store.vectorstore import collection

STOPWORDS = {"관련", "기업", "회사", "종목", "이후", "이전", "설립된", "설립", "대형주", "찾아줘", "있는", "하는"}


def keywords(condition: str) -> list[str]:
    return [w for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", condition) if w not in STOPWORDS]


def candidates(condition: str) -> dict[str, tuple[str, dict]]:
    """종목코드 → (색인 텍스트, 메타데이터). 키워드 일치 후보를 먼저, 그다음 벡터 검색 후보."""
    store = collection("meta")
    found: dict[str, tuple[str, dict]] = {}
    words = keywords(condition)
    if words:
        everything = store.get(include=["documents", "metadatas"])
        for text, meta in zip(everything["documents"], everything["metadatas"]):
            if any(w in text for w in words):
                found[meta["stock_code"]] = (text, meta)
    for d in store.similarity_search(condition, k=settings()["retrieval"]["explore_k"]):
        found.setdefault(d.metadata["stock_code"], (d.page_content, d.metadata))
    return found


def explore(condition: str) -> list[dict]:
    """[{stock_code, name, reason}] — 후보 밖의 종목코드는 버린다(LLM이 지어낸 코드 방지)."""
    found = candidates(condition)
    if not found:
        return []
    by_code = {code: meta for code, (_, meta) in found.items()}
    listing = "\n\n".join(f"- 종목코드 {code}\n{text}" for code, (text, _) in found.items())

    prompt = ChatPromptTemplate.from_messages([("system", prompts.EXPLORE_SYSTEM), ("human", prompts.EXPLORE_HUMAN)])
    result = (prompt | chat("select").with_structured_output(prompts.ExploreResult)).invoke(
        {"condition": condition, "candidates": listing}
    )
    picked = []
    for i, code in enumerate(result.stock_codes):
        code = code.strip().zfill(6)
        if code in by_code and code not in {p["stock_code"] for p in picked}:
            reason = result.reasons[i] if i < len(result.reasons) else ""
            picked.append({"stock_code": code, "name": by_code[code]["name"], "reason": reason})
    return picked
