"""답변 본문의 [근거 N] 표기를 찾아 실제로 인용된 근거만 남긴다. (F2008)"""

import re

# [근거 3], [근거 1, 3], [근거 1,3]
CITE_PATTERN = re.compile(r"\[근거\s*([\d,\s]+)\]")

SOURCE_LABELS = {
    "meta": "기업정보(DART)",
    "wiki": "위키백과",
    "dart": "DART 공시",
    "glossary": "한국은행 용어집",
}


def cited_numbers(answer: str) -> set[int]:
    return {
        int(n) for group in CITE_PATTERN.findall(answer)
        for n in group.split(",") if n.strip().isdigit()
    }


def cited_sources(answer: str, contexts: list[dict]) -> list[dict]:
    """contexts의 번호(n)는 retrieve에서 고정된다. 그 번호로만 매칭해 화면과 본문이 어긋나지 않게 한다."""
    numbers = cited_numbers(answer)
    return [c for c in contexts if c["n"] in numbers]
