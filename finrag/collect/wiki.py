"""한국어 위키백과 API. 교육생 배포 step8_wiki_api.py의 search/fetch_page를 바탕으로 했다."""

import html
import re

from finrag.collect.http import get
from finrag.config import env

API_URL = "https://ko.wikipedia.org/w/api.php"
SOURCE_NAME = "한국어 위키백과"
LICENSE = "CC BY-SA 4.0"


def user_agent() -> str:
    """연락처가 있으면 분당 200회, 없으면 10회. 헤더라 한글은 쓸 수 없다."""
    contact = env("WIKI_CONTACT", required=False)
    agent = f"FinRAG/0.1 ({contact}) python-requests" if contact else "FinRAG/0.1 python-requests"
    agent.encode("latin-1")  # 한글이 섞였으면 여기서 바로 실패시킨다
    return agent


def _query(params: dict) -> dict:
    base = {"action": "query", "format": "json", "formatversion": 2}
    return get(API_URL, {**base, **params}, headers={"User-Agent": user_agent()}).json()


def search(query: str, limit: int = 5) -> list[dict]:
    """검색어로 후보 문서를 찾는다. 본문 없이 제목과 짧은 요약만."""
    data = _query({"list": "search", "srsearch": query, "srlimit": limit})
    return [
        {
            "page_id": item["pageid"],
            "title": item["title"],
            "snippet": html.unescape(re.sub(r"<[^>]+>", "", item.get("snippet", ""))),
        }
        for item in data["query"]["search"]
    ]


def page_info(title: str) -> dict | None:
    """넘겨주기를 따라간 실제 문서 정보. 없으면 None. 동음이의어 문서면 disambiguation=True."""
    data = _query({
        "titles": title, "redirects": 1, "prop": "info|pageprops",
        "inprop": "url", "ppprop": "disambiguation",
    })
    page = data["query"]["pages"][0]
    if page.get("missing") or page.get("invalid"):
        return None
    return {
        "page_id": page["pageid"],
        "title": page["title"],
        "url": page["fullurl"],
        "disambiguation": "disambiguation" in page.get("pageprops", {}),
    }


def fetch_page(title: str, intro_only: bool = False) -> dict | None:
    """문서 본문(평문)과 원문 URL. 없는 문서나 동음이의어 문서면 None."""
    params = {"titles": title, "redirects": 1, "prop": "extracts|info|pageprops", "explaintext": 1,
              "inprop": "url", "ppprop": "disambiguation"}
    if intro_only:
        params["exintro"] = 1
    data = _query(params)
    page = data["query"]["pages"][0]
    if page.get("missing") or page.get("invalid"):
        return None
    if "disambiguation" in page.get("pageprops", {}):  # 예: '기아'는 기업이 아니라 동음이의어 문서
        return None
    return {
        "page_id": page["pageid"],
        "title": page["title"],
        "url": page["fullurl"],
        "text": page.get("extract", ""),
    }
