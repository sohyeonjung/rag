"""키워드(BM25) 검색 — 벡터 검색과 합쳐 쓰는 하이브리드 검색의 키워드 쪽.

골든셋에서 정답 청크를 못 찾은 7문항이 모두 DART 공시 청크였다. 공시는 표가 많고 길어서 임베딩 유사도에서 밀리지만,
질문에는 "ERICA", "상각형조건부자본증권", "매매목적거래"처럼 원문과 똑같은 용어가 들어 있다 → 글자 일치로 찾는다.

외부 라이브러리 없이 구현한다(명세서 고정 버전 목록 밖 라이브러리를 늘리지 않기 위해).
  - 한국어: 조사가 붙어도 맞도록 글자 2개씩 자른 bigram + 단어 전체
  - 숫자: 쉼표를 지운 숫자 덩어리 그대로 ("17,595" = "17595")
  - 영문: 소문자 단어
검색 범위는 선택 기업의 청크뿐이라(기업당 수십~수백 개) 질문마다 메모리에서 계산해도 충분히 빠르다.
"""

import math
import re
from collections import Counter
from functools import lru_cache

from finrag.store.vectorstore import collection

TOKEN = re.compile(r"[가-힣]+|[A-Za-z]+|\d[\d,.]*")
K1, B = 1.5, 0.75


def tokenize(text: str) -> list[str]:
    tokens = []
    for t in TOKEN.findall(text.lower()):
        if t[0].isdigit():
            tokens.append(t.replace(",", "").rstrip("."))
        elif "가" <= t[0] <= "힣":
            tokens.append(t)
            tokens += [t[i:i + 2] for i in range(len(t) - 1)]
        elif len(t) > 1:
            tokens.append(t)
    return tokens


@lru_cache(maxsize=32)
def _index(stock_code: str, n_chunks: int) -> tuple:
    """기업별 BM25 색인. 청크 수가 바뀌면(새로 수집하면) 키가 달라져 다시 만든다."""
    got = collection("docs").get(where={"stock_code": stock_code}, include=["documents", "metadatas"])
    docs = [tokenize(t) for t in got["documents"]]
    df = Counter(tok for d in docs for tok in set(d))
    avgdl = sum(len(d) for d in docs) / max(len(docs), 1)
    return got["ids"], got["documents"], got["metadatas"], [Counter(d) for d in docs], [len(d) for d in docs], df, avgdl


def search(stock_code: str, query: str, k: int) -> list[tuple[str, str, dict]]:
    """[(청크 ID, 본문, 메타데이터)] BM25 점수 상위 k개 (점수 0은 제외)."""
    n_chunks = len(collection("docs").get(where={"stock_code": stock_code}, include=[])["ids"])
    if not n_chunks:
        return []
    ids, texts, metas, tfs, lens, df, avgdl = _index(stock_code, n_chunks)
    q = set(tokenize(query))
    n = len(ids)
    scores = []
    for i, tf in enumerate(tfs):
        s = 0.0
        for tok in q:
            f = tf.get(tok)
            if f:
                idf = math.log(1 + (n - df[tok] + 0.5) / (df[tok] + 0.5))
                s += idf * f * (K1 + 1) / (f + K1 * (1 - B + B * lens[i] / avgdl))
        if s > 0:
            scores.append((s, i))
    scores.sort(reverse=True)
    return [(ids[i], texts[i], metas[i]) for _, i in scores[:k]]


def rrf(rankings: list[list[str]], k: int = 60) -> list[str]:
    """Reciprocal Rank Fusion — 여러 순위 목록을 점수 척도와 상관없이 순위만으로 합친다."""
    score: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            score[doc_id] = score.get(doc_id, 0.0) + 1 / (k + rank)
    return sorted(score, key=lambda d: -score[d])
