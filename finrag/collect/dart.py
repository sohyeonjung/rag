"""OpenDART API. 인증키가 URL 쿼리(crtfc_key)에 들어가므로 URL을 그대로 로그에 남기지 않는다."""

import io
import json
import xml.etree.ElementTree as ET
import zipfile

from finrag.collect.http import ExternalAPIError, get
from finrag.config import ROOT, env

BASE = "https://opendart.fss.or.kr/api"
SOURCE_NAME = "금융감독원 전자공시시스템(DART)"
CORP_CODE_CACHE = ROOT / "data" / "raw" / "dart_corp_codes.json"


def _check_status(data: dict) -> dict:
    status = data.get("status", "000")
    if status != "000":
        raise ExternalAPIError(f"DART 오류 {status}: {data.get('message', '')}")
    return data


def corp_codes() -> dict[str, dict]:
    """종목코드 → {corp_code, corp_name}. 전체 법인 목록(zip)은 한 번 받아 캐시한다."""
    if CORP_CODE_CACHE.exists():
        return json.loads(CORP_CODE_CACHE.read_text(encoding="utf-8"))

    response = get(f"{BASE}/corpCode.xml", {"crtfc_key": env("DART_API_KEY")}, timeout=120)
    if not response.content.startswith(b"PK"):  # 오류면 zip 대신 XML/JSON 메시지가 온다
        raise ExternalAPIError("DART 고유번호 목록을 받지 못했습니다. 인증키를 확인하세요.")
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        root = ET.fromstring(z.read(z.namelist()[0]))

    mapping = {}
    for node in root.iter("list"):
        stock_code = (node.findtext("stock_code") or "").strip()
        if stock_code:
            mapping[stock_code] = {
                "corp_code": node.findtext("corp_code").strip(),
                "corp_name": node.findtext("corp_name").strip(),
            }
    CORP_CODE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    CORP_CODE_CACHE.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
    return mapping


def company(corp_code: str) -> dict:
    """기업개황: 정식명칭, 영문명, 대표자, 법인구분, 주소, 홈페이지, 업종코드, 설립일, 결산월 등."""
    response = get(f"{BASE}/company.json", {"crtfc_key": env("DART_API_KEY"), "corp_code": corp_code})
    return _check_status(response.json())
