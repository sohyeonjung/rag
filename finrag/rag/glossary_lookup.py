"""질문에 쓸 용어집 근거 찾기.

임베딩만 쓰면 "공매도가 뭐야?"에서 '공매도'가 3위, "PER이 뭐야"는 관련도 0.33 수준이라 기준값을 정할 수 없었다.
그래서 질문에 용어가 그대로 나오면 먼저 잡고(키워드), 그다음 임베딩 결과를 기준값 이상일 때만 보탠다. (트러블슈팅 #6)
"""

import re
from functools import lru_cache

from finrag.config import settings
from finrag.store.vectorstore import collection


def _names(term: str) -> list[str]:
    """'기업분할(물적분할, 인적분할)' → ['기업분할', '물적분할', '인적분할'], '주가수익비율(PER)' → [..., 'PER']"""
    head, _, inner = term.partition("(")
    parts = [head] + re.split(r"[,/]", inner.rstrip(")"))
    out = []
    for p in parts:
        out += [x.strip() for x in p.split("/")]
    return [n for n in out if len(n) >= 2]


@lru_cache
def _index() -> list[tuple[str, str]]:
    """(이름, 청크 ID) — 긴 이름부터 (짧은 이름이 긴 용어 안에서 먼저 잡히지 않게)."""
    got = collection("glossary").get(include=["metadatas"])
    pairs = [(name, doc_id) for doc_id, meta in zip(got["ids"], got["metadatas"])
             if doc_id.endswith(":0") for name in _names(meta["term"])]
    return sorted(pairs, key=lambda p: -len(p[0]))


def _mentioned(question: str, name: str) -> bool:
    if re.fullmatch(r"[A-Za-z0-9+&\-. ]+", name):  # 영문 약어는 단어 경계로 (예: 'PER'이 'PERCENT'에 걸리지 않게)
        return re.search(rf"(?<![A-Za-z]){re.escape(name)}(?![A-Za-z])", question, flags=re.I) is not None
    return name in question


def lookup(question: str, k: int, min_score: float | None) -> list[tuple[str, str, dict]]:
    """[(청크 ID, 본문, 메타데이터)] 최대 k개."""
    store = collection("glossary")
    if store._collection.count() == 0:
        return []
    ids: list[str] = []
    for name, doc_id in _index():
        if len(ids) >= k:
            break
        if doc_id not in ids and _mentioned(question, name):
            ids.append(doc_id)

    hits = []
    if ids:
        got = store.get(ids=ids, include=["documents", "metadatas"])
        hits = list(zip(got["ids"], got["documents"], got["metadatas"]))
    if len(hits) < k:
        threshold = settings()["retrieval"]["glossary_min_score"] if min_score is None else min_score
        for doc, score in store.similarity_search_with_relevance_scores(question, k=k):
            doc_id = f"term:{doc.metadata['doc_id'][4:]}:{doc.metadata['seq']}"
            if score >= threshold and doc_id not in {h[0] for h in hits} and len(hits) < k:
                hits.append((doc_id, doc.page_content, doc.metadata))
    return hits
