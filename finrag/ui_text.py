"""답변을 화면(Markdown)에 그리기 전에 고치는 것들. Streamlit 없이 테스트할 수 있게 app.py에서 분리했다."""

import re

# 굵게 표시 안쪽 끝에 붙은 따옴표·괄호. 이 문자가 '**' 바로 안쪽에 있고 바깥에 한글이 붙으면
# Markdown 규칙상 '**'가 굵게 표시의 시작·끝으로 인정되지 않아 별표가 그대로 보인다.
#   **“삼성전자는 1969년에 설립”**은  →  “**삼성전자는 1969년에 설립**”은
_EDGE = "“”‘’\"'()[]{}<>「」『』《》〈〉.,:;!?·…"
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", re.S)


def _move_edge_punct(m: re.Match) -> str:
    inner = m.group(1)
    core = inner.strip(_EDGE)
    if not core:
        return m.group(0)
    start = inner.index(core)
    return f"{inner[:start]}**{core}**{inner[start + len(core):]}"


def safe_md(text: str) -> str:
    """LLM 답변을 Markdown으로 안전하게 그리기 위한 정리.

    - 굵게 표시 경계의 따옴표·괄호를 '**' 바깥으로 옮긴다 (** **가 그대로 보이던 문제)
    - '2016년~2020년'처럼 물결표가 두 번 나오면 취소선이 되므로 이스케이프한다 (트러블슈팅 #8)
    """
    return _BOLD.sub(_move_edge_punct, text).replace("~", r"\~")
