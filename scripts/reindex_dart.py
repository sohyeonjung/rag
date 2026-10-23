"""이미 색인한 DART 공시를 현재 청킹 규칙으로 다시 색인한다. 원문은 data/raw/dart 캐시를 쓴다.

    python scripts/reindex_dart.py            # 공시를 색인한 모든 기업
    python scripts/reindex_dart.py 035420     # 일부 기업만

청킹 규칙을 바꾸면 청크 ID(순번)가 달라지므로, 골든셋의 공시 정답 청크는 scripts/remap_golden_dart.py로 다시 맞춘다.
"""

import _bootstrap  # noqa: F401

import json
import sys

from finrag.collect.documents import index_dart
from finrag.config import path
from finrag.domain.companies import item_by_code
from finrag.store.vectorstore import collection


def main(codes: list[str]) -> None:
    docs = collection("docs")
    data = json.loads(path("collect_cache").read_text(encoding="utf-8"))
    targets = codes or [c for c, e in data.items() if e.get("dart_rcept")]
    for code in targets:
        item = item_by_code(code)
        old = docs.get(where={"$and": [{"stock_code": code}, {"source_type": "dart"}]}, include=[])["ids"]
        if old:
            docs.delete(ids=old)
        data = json.loads(path("collect_cache").read_text(encoding="utf-8"))
        data.get(code, {}).pop("dart_rcept", None)
        path("collect_cache").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        name, n = index_dart(item, progress=lambda _: None)
        print(f"{item['name']}: {len(old)} → {n}청크 ({name})")


if __name__ == "__main__":
    main(sys.argv[1:])
