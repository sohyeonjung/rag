"""F2014 — 같은 질문에 유사도 검색과 MMR 검색을 적용해 근거 목록을 비교한다.

    python scripts/compare_mmr.py              # 결과를 docs/eval/mmr_comparison.md 로 저장

답변은 만들지 않고 retrieve 단계만 돌린다 (LLM 크레딧 절약).
비교 지표
  - 서로 다른 문서 수 : 근거가 몇 개 문서에서 왔는가 (클수록 다양)
  - 평균 중복도       : 근거 청크끼리의 평균 코사인 유사도 (작을수록 내용이 덜 겹침)
  - 겹치는 근거 수    : 두 방식이 똑같이 고른 청크 수
"""

import _bootstrap  # noqa: F401

import math
from datetime import date

from finrag.collect.documents import collect
from finrag.config import ROOT, settings
from finrag.domain.companies import item_by_code
from finrag.llm import embeddings
from finrag.rag.graph import retrieve

CASES = [
    ("005930", "삼성전자의 반도체 사업은 어떻게 성장해 왔나?"),
    ("000660", "SK하이닉스의 HBM 사업은 어떤 내용이야?"),
    ("373220", "LG에너지솔루션은 언제 어떤 방식으로 LG화학에서 분할됐나?"),
    ("005380", "현대자동차의 전기차 전략은?"),
]


def redundancy(texts: list[str]) -> float:
    """청크 쌍의 평균 코사인 유사도."""
    if len(texts) < 2:
        return 0.0
    vecs = embeddings().embed_documents(texts)
    norms = [math.sqrt(sum(x * x for x in v)) for v in vecs]
    sims = [
        sum(a * b for a, b in zip(vecs[i], vecs[j])) / (norms[i] * norms[j])
        for i in range(len(vecs)) for j in range(i + 1, len(vecs))
    ]
    return sum(sims) / len(sims)


def run(code: str, question: str, search_type: str) -> list[dict]:
    item = item_by_code(code)
    state = {"stock_code": code, "company": item["name"], "question": question, "search_type": search_type}
    return [c for c in retrieve(state)["contexts"] if c["source_type"] != "meta"]  # 메타 1청크는 둘 다 같다


def main() -> None:
    conf = settings()["retrieval"]
    lines = [
        "# F2014 유사도 검색 vs MMR 검색 비교",
        "",
        f"- 실행일: {date.today().isoformat()}",
        f"- 설정: top_k={conf['top_k']}, own_doc_k={conf['own_doc_k']}, "
        f"MMR fetch_k={conf['fetch_k']}, lambda={conf['mmr_lambda']}",
        "- 메타데이터 청크(항상 포함)는 두 방식이 같아서 표에서 뺐다.",
        "",
        "| 기업 | 질문 | 방식 | 근거 청크 수 | 서로 다른 문서 수 | 평균 중복도 | 겹치는 근거 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    details = ["", "## 근거 목록", ""]

    for code, question in CASES:
        item = item_by_code(code)
        print(f"[{item['name']}] 수집 확인...")
        collect(item, question)  # 이미 수집했으면 캐시로 건너뛴다
        sim = run(code, question, "similarity")
        mmr = run(code, question, "mmr")
        common = len({c["id"] for c in sim} & {c["id"] for c in mmr})
        for name, ctx in (("유사도", sim), ("MMR", mmr)):
            docs = len({c["title"] for c in ctx})
            red = redundancy([c["text"] for c in ctx])
            lines.append(f"| {item['name']} | {question} | {name} | {len(ctx)} | {docs} | {red:.3f} | {common} |")
            details.append(f"**{item['name']} · {name}**: " + " / ".join(
                f"{c['title']}#{c['id'].rsplit(':', 1)[-1]}" for c in ctx))
            details.append("")
            print(f"  {name:<4} 문서 {docs}개 · 중복도 {red:.3f} · {[c['title'] for c in ctx]}")

    out = ROOT / "docs" / "eval" / "mmr_comparison.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines + details) + "\n", encoding="utf-8")
    print(f"\n저장: {out}")


if __name__ == "__main__":
    main()
