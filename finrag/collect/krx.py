"""코스피 시가총액 상위 종목 선정.

KRX 계열 데이터는 공공누리 4유형(재배포 금지)이라 원본은 data/raw/ 에만 두고 커밋하지 않는다.
결과로는 종목코드·종목명·기준일만 쓴다. 시세 값은 저장소에 남기지 않는다.

두 가지 방법 중 하나:
  1) 공공데이터포털 금융위원회_주식시세정보 API (DATA_GO_KR_KEY)
  2) KRX 정보데이터시스템에서 받은 시가총액 CSV를 data/raw/krx_marketcap.csv 로 저장
"""

import csv
from datetime import date, timedelta

from finrag.collect.http import ExternalAPIError, get
from finrag.config import ROOT, env

PRICE_API = "https://apis.data.go.kr/1160100/GetStockSecuritiesInfoService_V2/getStockPriceInfo_V2"
CSV_PATH = ROOT / "data" / "raw" / "krx_marketcap.csv"


def _is_common_stock(code: str, name: str) -> bool:
    """우선주 제외: 보통주는 종목코드 끝자리가 0이고, 우선주는 이름이 '우', '우B' 등으로 끝난다."""
    return code.endswith("0") and not name.rstrip().endswith(("우", "우B", "우C", "(전환)"))


def _from_api(n: int) -> tuple[list[dict], str]:
    key = env("DATA_GO_KR_KEY")
    day = date.today()
    for _ in range(10):  # 영업일 다음 날 13시 이후 갱신 → 최근 영업일을 거꾸로 찾는다
        day -= timedelta(days=1)
        params = {
            "serviceKey": key, "resultType": "json", "numOfRows": 1000, "pageNo": 1,
            "basDt": day.strftime("%Y%m%d"), "mrktCls": "KOSPI",
        }
        body = get(PRICE_API, params).json()["response"]["body"]
        items = (body.get("items") or {}).get("item") or []
        if items:
            rows = [
                {"stock_code": it["srtnCd"], "name": it["itmsNm"], "market_cap": int(it["mrktTotAmt"])}
                for it in items
            ]
            return rows, day.isoformat()
    raise ExternalAPIError("최근 10일 안에 시세 데이터를 찾지 못했습니다.")


def _from_csv() -> tuple[list[dict], str]:
    for encoding in ("utf-8-sig", "cp949"):
        try:
            with open(CSV_PATH, encoding=encoding, newline="") as f:
                reader = list(csv.DictReader(f))
            break
        except UnicodeDecodeError:
            continue
    rows = []
    for r in reader:
        market = r.get("시장구분", "KOSPI")
        if market not in ("KOSPI", "유가증권"):
            continue
        rows.append({
            "stock_code": r["종목코드"].strip().zfill(6),
            "name": r["종목명"].strip(),
            "market_cap": int(r["시가총액"].replace(",", "")),
        })
    return rows, f"CSV({CSV_PATH.name})"


def top_kospi(n: int = 100) -> tuple[list[dict], str]:
    """시가총액 상위 n개 보통주. (종목 목록, 기준일 설명)을 돌려준다."""
    rows, as_of = _from_csv() if CSV_PATH.exists() else _from_api(n)
    rows = [r for r in rows if _is_common_stock(r["stock_code"], r["name"])]
    rows.sort(key=lambda r: r["market_cap"], reverse=True)
    return [{"stock_code": r["stock_code"], "name": r["name"], "rank": i}
            for i, r in enumerate(rows[:n], start=1)], as_of
