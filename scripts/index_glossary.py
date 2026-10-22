"""한국은행 「경제금융용어 800선」 색인 (F2016). 용어 1개 = 청크 1개. 다시 실행해도 덮어쓰기만 한다.

    python scripts/index_glossary.py            # 파싱 + 색인
    python scripts/index_glossary.py --dry-run  # 파싱 결과만 확인
"""

import _bootstrap  # noqa: F401

import argparse
import warnings

from langchain_core.documents import Document

from finrag.collect import glossary
from finrag.store.chunking import split
from finrag.store.vectorstore import collection, count

warnings.filterwarnings("ignore")
BOK_URL = "https://www.bok.or.kr/portal/main/main.do"  # 용어별 원문 링크가 없어 한국은행 홈페이지 + 쪽 번호로 출처 표시


def main(dry_run: bool) -> None:
    entries = glossary.parse()
    print(f"파싱한 용어 {len(entries)}개")
    if dry_run:
        for e in entries[:3]:
            print(f"- {e['term']} (p.{e['page']}) 연관: {e['related']}\n  {e['text'][:120]}...")
        return

    docs, ids = [], []
    for i, e in enumerate(entries):
        for seq, chunk in enumerate(split(e["text"], "glossary")):
            ids.append(f"term:{i:03d}:{seq}")
            docs.append(Document(page_content=f"{e['term']}\n{chunk}", metadata={
                "term": e["term"], "page": e["page"], "related": e["related"],
                "source_type": "glossary", "title": f"{e['term']} (경제금융용어 800선 p.{e['page']})",
                "source_url": BOK_URL, "doc_id": f"term{i:03d}", "seq": seq, "doc_date": "2026-01-29",
            }))
    before = count("glossary")
    store = collection("glossary")
    for start in range(0, len(docs), 200):  # 한 번에 너무 많이 보내지 않게 나눠서
        store.add_documents(docs[start:start + 200], ids=ids[start:start + 200])
    print(f"색인 전 {before}청크 → 색인 후 {count('glossary')}청크")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    main(parser.parse_args().dry_run)
