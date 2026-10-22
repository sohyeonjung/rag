"""터미널에서 수집 → 답변 전체 흐름을 확인한다 (검증 4.2 · 4.3).

    python scripts/try_question.py 373220 "LG에너지솔루션은 언제 어떤 방식으로 LG화학에서 분할됐나?"
    python scripts/try_question.py 005930 "이 회사가 사업보고서에서 말하는 리스크는?" --task briefing
    python scripts/try_question.py 005930 "..." --search-type mmr --top-k 10
"""

import _bootstrap  # noqa: F401

import argparse
import time

from finrag.collect.documents import collect
from finrag.domain.companies import item_by_code
from finrag.rag.graph import ask


def main(args) -> None:
    item = item_by_code(args.stock_code)
    if not item:
        raise SystemExit(f"{args.stock_code} 종목이 목록에 없습니다.")

    t0 = time.perf_counter()
    report = collect(item, args.question, progress=lambda m: print("  ·", m))
    t1 = time.perf_counter()
    print(f"\n[수집] {t1 - t0:.1f}초")
    print("  검색어 (P1)      :", report.queries)
    print("  캐시로 건너뜀    :", report.skipped_queries)
    print("  색인한 문서 (P2) :", report.selected, f"({report.chunks}청크)")
    print("  버린 후보        :", report.rejected)
    print("  선별 이유        :", report.select_reason)

    result = ask(item["stock_code"], item["name"], args.question, task=args.task,
                 search_type=args.search_type, top_k=args.top_k)
    t2 = time.perf_counter()
    print(f"\n[답변] {t2 - t1:.1f}초 · 전체 {t2 - t0:.1f}초")
    print("  검색된 근거:", [f"{c['n']}:{c['title']}" for c in result["contexts"]])
    print("\n" + result["answer"])
    print("\n[인용된 근거]")
    for c in result["cited"] or []:
        print(f"  [근거 {c['n']}] {c['title']} · {c['source_type']} · {c['url']}")
    if not result["cited"]:
        print("  (인용 없음)")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("stock_code")
    p.add_argument("question")
    p.add_argument("--task", default="qa", choices=["qa", "briefing", "factcheck"])
    p.add_argument("--search-type", default=None, choices=["similarity", "mmr"])
    p.add_argument("--top-k", type=int, default=None)
    main(p.parse_args())
