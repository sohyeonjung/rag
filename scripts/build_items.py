"""항목 데이터 구축: 코스피 시가총액 상위 100개 → DART 기업개황 → 위키 문서 매핑 → data/items.json

    python scripts/build_items.py            # 100개
    python scripts/build_items.py --n 10     # 빠른 확인용

필요: DART_API_KEY, (DATA_GO_KR_KEY 또는 data/raw/krx_marketcap.csv), WIKI_CONTACT 권장
"""

import _bootstrap  # noqa: F401

import argparse
import json
from datetime import date

from finrag.collect import dart, krx, wiki
from finrag.collect.http import ExternalAPIError
from finrag.config import path
from finrag.domain.companies import ALIASES, WIKI_TITLE_OVERRIDES


def resolve_wiki_title(name: str, corp_name: str) -> tuple[dict | None, str]:
    """기업 본문서를 찾는다. (문서 정보, 찾은 방법). 동음이의어 문서는 건너뛴다.

    '{이름} (기업)'을 먼저 본다. 지주회사(LG, CJ)는 이름만으로 찾으면 그룹 문서로 넘어가기 때문이다.
    """
    candidates = [WIKI_TITLE_OVERRIDES.get(name), f"{name} (기업)", name, corp_name]
    for title in dict.fromkeys(c for c in candidates if c):
        info = wiki.page_info(title)
        if info and not info["disambiguation"]:
            return info, "override" if title == WIKI_TITLE_OVERRIDES.get(name) else "title"
    hits = wiki.search(f"{name} 기업", limit=3)
    for hit in hits:
        info = wiki.page_info(hit["title"])
        if info and not info["disambiguation"]:
            return info, "search"  # 사람이 한 번 확인해야 하는 매핑
    return None, "missing"


def first_sentences(text: str, n: int = 2) -> str:
    sentences = [s.strip() for s in text.replace("\n", " ").split(". ") if s.strip()]
    summary = ". ".join(sentences[:n])
    return summary if summary.endswith(".") else summary + "."


def main(n: int) -> None:
    top, as_of = krx.top_kospi(n)
    print(f"시가총액 상위 {len(top)}개 ({as_of} 기준)")
    codes = dart.corp_codes()

    items, review = [], []
    for row in top:
        code, name = row["stock_code"], row["name"]
        item = {"stock_code": code, "name": name, "rank": row["rank"], "rank_as_of": as_of,
                "aliases": ALIASES.get(name, [])}

        corp = codes.get(code)
        if corp:
            try:
                info = dart.company(corp["corp_code"])
                item.update({
                    "corp_code": corp["corp_code"],
                    "corp_name_full": info.get("corp_name", ""),
                    "name_eng": info.get("corp_name_eng", ""),
                    "ceo": info.get("ceo_nm", ""),
                    "address": info.get("adres", ""),
                    "homepage": info.get("hm_url", ""),
                    "induty_code": info.get("induty_code", ""),
                    "est_dt": info.get("est_dt", ""),
                    "acc_mt": info.get("acc_mt", ""),
                    "dart_url": f"https://dart.fss.or.kr/dsab007/main.do?autoSearch=true&textCrpCik={corp['corp_code']}",
                })
            except ExternalAPIError as e:
                review.append(f"{name}: DART 기업개황 실패 ({e})")
        else:
            review.append(f"{name}: DART 고유번호 없음")

        page, how = resolve_wiki_title(name, item.get("corp_name_full", ""))
        if page:
            intro = wiki.fetch_page(page["title"], intro_only=True) or {}
            item.update({"wiki_title": page["title"], "wiki_url": page["url"],
                         "summary": first_sentences(intro.get("text", ""))})
            if how == "search":
                review.append(f"{name}: 위키 문서를 검색으로 찾음 → '{page['title']}' 확인 필요")
            elif page["title"].endswith("그룹") and how != "override":
                review.append(f"{name}: 그룹 문서로 연결됨 → '{page['title']}' 확인 필요")
        else:
            review.append(f"{name}: 위키 문서 없음")

        items.append(item)
        print(f"  {row['rank']:>3}. {name:<14} {item.get('wiki_title', '-')}")

    out = path("items")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "built_at": date.today().isoformat(),
        "rank_as_of": as_of,
        "sources": {
            "기업 목록": "KRX 시세(공공데이터포털 금융위원회_주식시세정보 또는 KRX 정보데이터시스템) — 종목코드·종목명·순위만 사용",
            "기업개황": f"{dart.SOURCE_NAME} OpenDART 기업개황 API",
            "개요": f"{wiki.SOURCE_NAME} ({wiki.LICENSE})",
        },
        "items": items,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n저장: {out} ({len(items)}건)")
    if review:
        print("\n확인 필요:")
        for line in review:
            print("  -", line)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=100)
    main(parser.parse_args().n)
