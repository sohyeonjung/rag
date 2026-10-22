"""한국은행 「경제금융용어 800선」(2026) PDF 파서 (F2016 — 세 번째 문서 출처).

PDF 원본은 저작권이 한국은행에 있어 저장소에 넣지 않는다. data/raw/ 에 직접 내려받아 둔다.

구조
  - 목차: '가산금리· ·········· 4' 처럼 용어와 쪽 번호가 점선으로 이어진다 (공백은 '·'로 표기)
  - 본문: 쪽마다 머리글(용어) · 자음(ㄱ) · 쪽 번호 3줄이 먼저 나오고,
          용어 한 줄 → 해설 여러 줄 → ' 연관검색어   ...' 순서로 이어진다
파싱 전략
  본문에서 아무 줄이나 용어와 비교하면 해설 속 짧은 줄이 용어로 잘못 잡힌다.
  그래서 목차 순서대로 '다음에 나올 용어'만 기다리며 경계를 찾는다.
"""

import re
from pathlib import Path

from pypdf import PdfReader

from finrag.config import ROOT

RAW_DIR = ROOT / "data" / "raw"
SOURCE_NAME = "한국은행 경제금융용어 800선 (2026)"
TOC_LINE = re.compile(r"^(?P<term>.+?)[·\s]*·{3,}[·\s]*(?P<page>[\d\s]+)$")
RELATED = re.compile(r"^\s*연관검색어\s+(?P<terms>.+)$")
LOOKAHEAD = 3  # 목차 용어가 본문에서 한두 개 누락돼도 따라잡는다


def find_pdf() -> Path | None:
    pdfs = sorted(RAW_DIR.glob("*800선*.pdf")) or sorted(RAW_DIR.glob("*.pdf"))
    return pdfs[0] if pdfs else None


def _norm(s: str) -> str:
    """비교용: 공백·가운뎃점 제거."""
    return re.sub(r"[\s·]", "", s)


FOOTER = re.compile(r"^I?\s*경제금융용어\s*800선$|^찾아보기")
TOC_NOISE = re.compile(r"^([ivx]+|[ㄱ-ㅎ]|[A-Z]{1,3}|I?\s*경제금융용어\s*800선|찾아보기.*)$")


def _toc(pages: list[str]) -> tuple[list[dict], int]:
    """목차 용어 목록과 본문이 시작하는 쪽(0부터)을 돌려준다.

    긴 용어는 목차에서 두 줄로 나뉜다 ('경제활동인구/비경제활동인구/·' + '경제활동참가율·····17').
    점선이 없는 줄은 다음 줄 앞에 붙인다.
    """
    terms, last_toc_page, pending = [], 0, ""
    for i, text in enumerate(pages[:40]):
        found = False
        for raw in text.split("\n"):
            line = raw.strip()
            m = TOC_LINE.match(line)
            if m:
                found = True
                term = (pending + m["term"]).replace("·", " ")
                terms.append({"term": re.sub(r"\s+", " ", term).strip(), "page": int(re.sub(r"\s", "", m["page"]))})
                pending = ""
            elif found and line and not TOC_NOISE.match(line):
                pending += line
        if found:
            last_toc_page = i
    return terms, last_toc_page + 1


def _body_lines(pages: list[str]) -> list[str]:
    """본문 줄. 쪽 머리글을 뗀다. 줄 끝 공백은 단어 경계라서 남긴다.

    홀수 쪽: 머리글 용어 · 자음 · 쪽 번호 (뒤 두 줄은 순서가 바뀌기도 한다)
    짝수 쪽: 쪽 번호 한 줄로 시작하고, 끝에 'I 경제금융용어 800선' 꼬리말
    """
    lines = []
    for text in pages:
        rows = text.split("\n")
        head = [r.strip() for r in rows[:3]]
        if head and head[0].isdigit():
            rows = rows[1:]
        elif len(head) == 3 and any(h.isdigit() for h in head[1:]) and any(len(h) <= 2 and not h.isdigit() for h in head[1:]):
            rows = rows[3:]
        while rows and FOOTER.match(rows[-1].strip()):
            rows = rows[:-1]
        lines.extend(rows)
    return lines


def _join(lines: list[str]) -> str:
    """PDF 줄바꿈 복원: 줄 끝에 공백이 있으면 띄어쓰기, 없으면 단어 중간에서 끊긴 것으로 보고 붙인다."""
    out = ""
    for line in lines:
        out += line if out.endswith((" ", "\n")) or not out else line
    return re.sub(r"[ \t]+", " ", out).strip()


def parse(pdf: Path | None = None) -> list[dict]:
    """[{term, page, text, related}] — 목차 순서."""
    pdf = pdf or find_pdf()
    if not pdf:
        raise FileNotFoundError("data/raw/ 에 경제금융용어 800선 PDF가 없습니다.")
    pages = [p.extract_text() or "" for p in PdfReader(str(pdf)).pages]
    toc, body_start = _toc(pages)
    lines = _body_lines(pages[body_start:])

    entries, current, buf = [], None, []
    nxt = 0  # 다음에 기다리는 목차 용어의 위치

    def close():
        if current is None:
            return
        related, text_lines = "", []
        for l in buf:
            m = RELATED.match(l)
            if m:
                related = re.sub(r"\s{2,}", ", ", m["terms"].strip())
            else:
                text_lines.append(l)
        text = _join(text_lines)
        if text:
            entries.append({**current, "text": text, "related": related})

    for line in lines:
        key = _norm(line)
        hit = next((j for j in range(nxt, min(nxt + LOOKAHEAD, len(toc)))
                    if key and key == _norm(toc[j]["term"])), None)
        if hit is not None:
            close()
            current, buf, nxt = toc[hit], [], hit + 1
        elif current is not None:
            buf.append(line)
    close()
    return entries
