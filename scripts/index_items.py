"""기업 메타데이터 색인 (F2001~F2002). 청크 ID = 종목코드라 다시 실행해도 덮어쓰기만 한다.

    python scripts/index_items.py
    python scripts/index_items.py --show 005930    # 항목 1건의 색인 텍스트 확인 (검증 4.1)
"""

import _bootstrap  # noqa: F401

import argparse

from langchain_core.documents import Document

from finrag.domain.companies import build_item_text, item_by_code, item_metadata, load_items
from finrag.store.vectorstore import collection, count


def main(show: str | None) -> None:
    items = load_items()
    if not items:
        raise SystemExit("data/items.json 이 없습니다. 먼저 python scripts/build_items.py 를 실행하세요.")

    if show:
        item = item_by_code(show)
        print(build_item_text(item) if item else f"{show} 종목이 목록에 없습니다.")
        return

    before = count("meta")
    docs = [Document(page_content=build_item_text(it), metadata=item_metadata(it)) for it in items]
    collection("meta").add_documents(docs, ids=[it["stock_code"] for it in items])
    print(f"로드한 항목 {len(items)}건 · 색인 전 {before}청크 → 색인 후 {count('meta')}청크")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--show", help="종목코드")
    main(parser.parse_args().show)
