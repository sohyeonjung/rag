"""잘못 수집된 외부 문서를 지운다 — 특정 기업에서 특정 검색어로 들어온 위키 문서만.

    python scripts/forget_docs.py 033780 삼성전자 서울특별시          # 미리 보기
    python scripts/forget_docs.py 033780 삼성전자 서울특별시 --apply  # 실제로 삭제

색인 청크와 수집 캐시(검색어·문서 기록)를 함께 지워서, 나중에 필요하면 다시 수집될 수 있게 한다.
"""

import _bootstrap  # noqa: F401

import argparse
import json

from finrag.collect import cache
from finrag.config import path
from finrag.store.vectorstore import collection


def main(stock_code: str, queries: list[str], apply: bool) -> None:
    docs = collection("docs")
    got = docs.get(where={"$and": [{"stock_code": stock_code}, {"source_type": "wiki"}]}, include=["metadatas"])
    targets = [(cid, m) for cid, m in zip(got["ids"], got["metadatas"]) if m.get("query") in queries]
    pages = {m["doc_id"]: m["title"] for _, m in targets}
    print(f"{stock_code}: 검색어 {queries} 로 들어온 문서 {len(pages)}건 · 청크 {len(targets)}개 — {sorted(pages.values())}")
    if not apply or not targets:
        print("(미리 보기 — 지우려면 --apply)" if targets else "지울 문서가 없습니다.")
        return

    docs.delete(ids=[cid for cid, _ in targets])
    data = json.loads(path("collect_cache").read_text(encoding="utf-8"))
    entry = data.get(stock_code, {})
    entry["queries"] = [q for q in entry.get("queries", []) if q not in queries]
    keep = [(pid, t) for pid, t in zip(entry.get("page_ids", []), entry.get("titles", [])) if str(pid) not in pages]
    entry["page_ids"], entry["titles"] = [p for p, _ in keep], [t for _, t in keep]
    path("collect_cache").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"삭제 완료 · 남은 수집 문서: {cache.get(stock_code)['titles']}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("stock_code")
    p.add_argument("queries", nargs="+", help="이 검색어로 들어온 위키 문서를 지운다")
    p.add_argument("--apply", action="store_true")
    a = p.parse_args()
    main(a.stock_code, a.queries, a.apply)
