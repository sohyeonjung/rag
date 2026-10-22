"""DART 정기보고서 본문 수집 (F2016 — 위키백과와 다른 종류의 문서 출처).

가장 최근 정기보고서(사업·반기·분기)의 'I. 회사의 개요'와 'II. 사업의 내용'만 쓴다.
보고서 전체는 수백 쪽이고 대부분 재무제표 주석이라, 다 넣으면 임베딩 비용과 검색 노이즈만 커진다.
위키(수집 시점 스냅샷)와 달리 공시는 접수일이 분명하므로 doc_date에 접수일을 넣는다.
"""

import html
import io
import re
import zipfile

from finrag.collect.http import ExternalAPIError, get
from finrag.config import ROOT, env

BASE = "https://opendart.fss.or.kr/api"
RAW_DIR = ROOT / "data" / "raw" / "dart"
PERIODIC = ("사업보고서", "반기보고서", "분기보고서")
KEEP_PARTS = ("I. 회사의 개요", "II. 사업의 내용")
SKIP_SUBSECTIONS = ("자본금 변동사항", "주식의 총수", "정관에 관한 사항")  # 질문 근거로 쓰일 일이 드묾


def latest_report(corp_code: str) -> dict | None:
    """가장 최근 정기보고서 {rcept_no, report_nm, rcept_dt}. 없으면 None."""
    data = get(f"{BASE}/list.json", {
        "crtfc_key": env("DART_API_KEY"), "corp_code": corp_code,
        "bgn_de": "20240101", "pblntf_ty": "A", "page_count": 20,
    }).json()
    if data.get("status") not in ("000", "013"):
        raise ExternalAPIError(f"DART 공시검색 오류 {data.get('status')}: {data.get('message', '')}")
    for row in data.get("list", []):  # 최신순
        if row["report_nm"].strip().startswith(PERIODIC):
            return {"rcept_no": row["rcept_no"], "report_nm": row["report_nm"].strip(), "rcept_dt": row["rcept_dt"]}
    return None


def _download(rcept_no: str) -> str:
    cached = RAW_DIR / f"{rcept_no}.xml"
    if cached.exists():
        return cached.read_text(encoding="utf-8", errors="replace")
    response = get(f"{BASE}/document.xml", {"crtfc_key": env("DART_API_KEY"), "rcept_no": rcept_no}, timeout=120)
    if not response.content.startswith(b"PK"):
        raise ExternalAPIError("DART 공시 원문을 받지 못했습니다.")
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        main = min(z.namelist(), key=len)  # 본문은 '{접수번호}.xml', 첨부는 '{접수번호}_00760.xml' 식
        raw = z.read(main)
    text = raw.decode("utf-8", errors="replace")
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    cached.write_text(text, encoding="utf-8")
    return text


def _plain(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _row(match: re.Match) -> str:
    cells = [_plain(c) for c in re.findall(r"<T[DHEU][^>]*>(.*?)</T[DHEU]>", match.group(0), flags=re.S)]
    return " | ".join(c for c in cells if c) + "\n"


def _clean(fragment: str) -> str:
    """XML 조각 → 평문. 표는 행마다 한 줄, 칸은 ' | '로 이어 문장처럼 읽히게 한다.

    칸 안에 <P>가 들어 있어 그냥 태그를 지우면 칸마다 줄이 바뀐다 → 행(<TR>) 단위로 먼저 평문화한다.
    """
    s = re.sub(r"<TR[^>]*>.*?</TR>", _row, fragment, flags=re.S)
    s = re.sub(r"</P>|<BR\s*/?>|<PGBRK[^>]*>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    lines = []
    for line in s.split("\n"):
        line = re.sub(r"\s+", " ", line).strip(" |")
        if line and line not in ("-", "|"):
            lines.append(line)
    return "\n".join(lines)


def sections(rcept_no: str) -> list[dict]:
    """[{section: 'II. 사업의 내용 > 1. 사업의 개요', text}] — 하위 목차 단위로 나눈다."""
    xml = _download(rcept_no)
    titles = list(re.finditer(r"<TITLE[^>]*>(.*?)</TITLE>", xml, flags=re.S))
    out, part = [], None
    for i, m in enumerate(titles):
        title = re.sub(r"\s+", " ", html.unescape(m.group(1))).strip()
        if re.match(r"^[IVX]+\.\s", title):  # 대목차
            part = title if title.startswith(KEEP_PARTS) else None
            continue
        if not part or any(skip in title for skip in SKIP_SUBSECTIONS):
            continue
        end = titles[i + 1].start() if i + 1 < len(titles) else len(xml)
        text = _clean(xml[m.end():end])
        if len(text) > 50:
            out.append({"section": f"{part} > {title}", "text": text})
    return out
