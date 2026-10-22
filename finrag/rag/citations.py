"""답변 본문의 [근거 N] 표기를 찾아 실제로 인용된 근거만 남긴다. (F2008)"""

import re

# [근거 3], [근거 1, 3], [근거 1,3], [근거 7, 근거 8] — 모델이 괄호 안에서 '근거'를 반복하기도 한다 (트러블슈팅 #8)
CITE_PATTERN = re.compile(r"\[근거\s*([\d,\s]+(?:근거\s*[\d,\s]+)*)\]")

SOURCE_LABELS = {
    "meta": "기업정보(DART)",
    "wiki": "위키백과",
    "dart": "DART 공시",
    "glossary": "한국은행 용어집",
}


# "제공된 문서([근거 1]~[근거 8])에서는 확인되지 않습니다" 같은 범위 표기는 인용이 아니라 '문서 전체'를 가리킨다.
# 프롬프트로 막아도 작은 모델이 계속 써서 코드에서 걸러낸다. (트러블슈팅 #8)
RANGE_PATTERN = re.compile(r"\[근거\s*\d+\]\s*[~\-–]\s*\[근거\s*\d+\]|\[근거\s*\d+\s*[~\-–]\s*\d+\]")


def cited_numbers(answer: str) -> set[int]:
    answer = RANGE_PATTERN.sub("", answer)
    return {int(n) for group in CITE_PATTERN.findall(answer) for n in re.findall(r"\d+", group)}


def cited_sources(answer: str, contexts: list[dict]) -> list[dict]:
    """contexts의 번호(n)는 retrieve에서 고정된다. 그 번호로만 매칭해 화면과 본문이 어긋나지 않게 한다."""
    numbers = cited_numbers(answer)
    return [c for c in contexts if c["n"] in numbers]
