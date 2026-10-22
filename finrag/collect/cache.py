"""수집 캐시: 기업별로 이미 검색한 검색어와 색인한 문서를 기록한다. (NF2002)"""

import json
from datetime import date

from finrag.config import path


def _load() -> dict:
    p = path("collect_cache")
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def get(stock_code: str) -> dict:
    return _load().get(stock_code, {"queries": [], "page_ids": [], "titles": []})


def update(stock_code: str, queries: list[str], pages: list[dict]) -> None:
    data = _load()
    entry = data.setdefault(stock_code, {"queries": [], "page_ids": [], "titles": []})
    entry["queries"] = sorted(set(entry["queries"]) | set(queries))
    for p in pages:
        if p["page_id"] not in entry["page_ids"]:
            entry["page_ids"].append(p["page_id"])
            entry["titles"].append(p["title"])
    entry["collected_at"] = date.today().isoformat()
    out = path("collect_cache")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def clear() -> None:
    path("collect_cache").unlink(missing_ok=True)
