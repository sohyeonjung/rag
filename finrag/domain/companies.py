"""항목(상장기업) 정의: 로드, 색인 텍스트 구성, 별칭, 위키 제목 보정."""

import json
from functools import lru_cache

from finrag.config import path

DOMAIN = "기업"
ITEM_LABEL = "상장기업"

# 종목명과 위키 문서 제목이 다른 경우. 동음이의어 문서로 가는 기업도 여기서 바로잡는다.
WIKI_TITLE_OVERRIDES = {
    "KB금융": "KB금융그룹",
    "신한지주": "신한금융그룹",
    "하나금융지주": "하나금융그룹",
    "우리금융지주": "우리금융그룹",
    "현대차": "현대자동차",
    "한국전력": "한국전력공사",
    "기아": "기아 (기업)",
    "HMM": "HMM (기업)",
    "SK": "SK (기업)",
    "GS": "GS (기업)",
    "효성": "효성 (기업)",
    "NC": "엔씨",                                    # 검색하면 야구단(NC 다이노스)이 먼저 나온다
    "메리츠금융지주": "메리츠금융그룹",              # 지주회사 문서가 그룹 제목으로 되어 있음
    "BNK금융지주": "BNK금융그룹",
    "LIG디펜스앤에어로스페이스": "LIG 디펜스&에어로스페이스",
    "삼성에피스홀딩스": "삼성바이오에피스",          # 지주회사 문서가 없어 핵심 자회사 문서로 대신함
}

# 사용자가 흔히 부르는 이름. 기업 선택 검색과 조건 탐색에서 함께 쓴다.
ALIASES = {
    "삼성전자": ["삼전"],
    "SK하이닉스": ["하이닉스", "SK hynix"],
    "현대차": ["현대자동차", "현차"],
    "NAVER": ["네이버"],
    "LG에너지솔루션": ["엘지엔솔", "LG엔솔"],
    "삼성바이오로직스": ["삼바"],
    "KB금융": ["KB금융그룹", "국민은행"],
    "신한지주": ["신한금융", "신한은행"],
    "하나금융지주": ["하나금융", "하나은행"],
    "우리금융지주": ["우리금융", "우리은행"],
    "한국전력": ["한전", "한국전력공사"],
}


@lru_cache
def load_items() -> list[dict]:
    """data/items.json — scripts/build_items.py 로 만든다."""
    p = path("items")
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))["items"]


def item_by_code(stock_code: str) -> dict | None:
    return next((it for it in load_items() if it["stock_code"] == stock_code), None)


def display_name(item: dict) -> str:
    return f"{item['name']} ({item['stock_code']})"


def _fmt_date(yyyymmdd: str) -> str:
    d = (yyyymmdd or "").strip()
    return f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 else "확인 불가"


def build_item_text(item: dict) -> str:
    """항목 1건 → 색인 텍스트 1개. (F2001)

    사용자가 무엇으로 기업을 찾을지 기준으로 속성을 고른다.
    위키 개요를 넣어야 "반도체 대형주"처럼 사업 내용으로 찾는 조건 탐색이 걸린다.
    """
    # 개요(사업 내용)를 이름 바로 다음에 둔다. 주소·대표자 같은 필드가 앞서면 임베딩이 사업 내용을
    # 덜 반영해 "반도체 관련 기업" 검색에서 SK하이닉스가 빠졌다. (트러블슈팅 #3)
    lines = [
        f"[기업] {item['name']} ({item.get('name_eng') or '영문명 없음'}) | 종목코드 {item['stock_code']} | 유가증권시장(코스피)",
        f"개요: {item.get('summary') or '없음'}",
        f"정식명칭: {item.get('corp_name_full') or item['name']} | 별칭: {', '.join(item.get('aliases', [])) or '없음'}",
        f"대표자: {item.get('ceo') or '확인 불가'} | 설립일: {_fmt_date(item.get('est_dt', ''))} | "
        f"결산월: {item.get('acc_mt') or '확인 불가'}월 | 본사: {item.get('address') or '확인 불가'}",
        f"시가총액 순위(코스피, {item.get('rank_as_of', '기준일 미상')} 기준): {item.get('rank', '?')}위",
    ]
    return "\n".join(lines)


def item_metadata(item: dict) -> dict:
    """Chroma 메타데이터는 스칼라만 허용 → 리스트는 문자열로, 연도·월은 숫자로."""
    est = (item.get("est_dt") or "")[:4]
    return {
        "stock_code": item["stock_code"],        # 반드시 문자열 (앞자리 0 유지)
        "corp_code": item.get("corp_code", ""),
        "name": item["name"],
        "aliases": ", ".join(item.get("aliases", [])),
        "est_year": int(est) if est.isdigit() else 0,
        "acc_month": int(item["acc_mt"]) if str(item.get("acc_mt", "")).isdigit() else 0,
        "rank": int(item.get("rank", 0)),
        "wiki_title": item.get("wiki_title", ""),
        "source_type": "meta",
        "title": f"{item['name']} 기업 정보",
        "source_url": item.get("dart_url", ""),
    }
