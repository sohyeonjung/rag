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


# 표 머리글 행과 표 끝 표시. 청킹(store/chunking.split_dart)이 이것을 보고 청크마다 머리글을 다시 붙인다.
# 긴 표가 여러 청크로 나뉘면 뒤쪽 청크에는 숫자 행만 남아 어느 값이 어느 열인지 알 수 없었다 (트러블슈팅 #13)
TABLE_HEAD = "[표 머리글] "
TABLE_END = "[표 끝]"


def _span(attrs: str, name: str) -> int:
    m = re.search(rf'{name}="(\d+)"', attrs)
    return max(int(m.group(1)), 1) if m else 1


def _table(match: re.Match) -> str:
    """표 하나 → 행마다 한 줄. 합친 칸(ROWSPAN·COLSPAN)을 펼쳐 모든 행의 칸 수를 머리글과 맞춘다.

    펼치지 않으면 '토지 | 본사 및 본사 외 | 등기 | 분당 | …' 다음 행이 '등기 | 세종 | …'처럼 앞 칸이 빠져,
    열 이름과 값이 어긋났다(프랑스 건설중인자산 '기말장부금액'을 묻자 다른 칸 값 0을 답함).
    여러 줄 머리글은 열마다 이어 붙인다: '당기증감' + '증가' → '당기증감-증가'.
    """
    grid: list[tuple[bool, list[str]]] = []
    carry: dict[int, tuple[str, int]] = {}  # 열 → (위에서 내려온 값, 남은 행 수)
    for tr in re.findall(r"<TR[^>]*>(.*?)</TR>", match.group(0), flags=re.S):
        cells = re.findall(r"<(T[DHEU])([^>]*)>(.*?)</\1>", tr, flags=re.S)
        if not cells:
            continue
        is_head = all(tag == "TH" for tag, _, _ in cells)
        row: list[str] = []

        def take_carried() -> None:
            while len(row) in carry:
                text, left = carry.pop(len(row))
                if left > 1:
                    carry[len(row)] = (text, left - 1)
                row.append(text)

        for _, attrs, body in cells:
            take_carried()
            text, rows, cols = _plain(body), _span(attrs, "ROWSPAN"), _span(attrs, "COLSPAN")
            for k in range(cols):
                value = text if k == 0 or is_head else ""  # 값 칸을 가로로 합친 경우는 한 번만 쓴다
                if rows > 1:
                    carry[len(row)] = (value, rows - 1)
                row.append(value)
        take_carried()
        grid.append((is_head, row))

    lines: list[str] = []
    i = 0
    while i < len(grid):
        if grid[i][0]:  # 연속된 머리글 행을 열 이름 한 줄로
            heads = []
            while i < len(grid) and grid[i][0]:
                heads.append(grid[i][1])
                i += 1
            width = max(len(h) for h in heads)
            names = []
            for j in range(width):
                parts = [h[j] for h in heads if j < len(h) and h[j]]
                names.append("-".join(dict.fromkeys(parts)))
            lines.append(TABLE_HEAD + " | ".join(names))
        else:
            lines.append(" | ".join(grid[i][1]))
            i += 1
    return "\n" + "\n".join(lines) + f"\n{TABLE_END}\n"


def _clean(fragment: str) -> str:
    """XML 조각 → 평문. 표는 행마다 한 줄, 칸은 ' | '로 이어 문장처럼 읽히게 한다.

    칸 안에 <P>가 들어 있어 그냥 태그를 지우면 칸마다 줄이 바뀐다 → 표(<TABLE>) 단위로 먼저 평문화한다.
    """
    s = re.sub(r"<TABLE[^>]*>.*?</TABLE>", _table, fragment, flags=re.S)
    s = re.sub(r"</P>|<BR\s*/?>|<PGBRK[^>]*>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    lines = []
    for line in s.split("\n"):
        line = re.sub(r"\s+", " ", line).strip(" |")
        if line and line not in ("-", "|", TABLE_HEAD.strip()):
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
