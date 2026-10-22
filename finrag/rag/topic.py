"""질문이 서비스 범위(상장기업·경제 용어) 안인지 먼저 거른다.

"소현아 로그 보고있니" 같은 잡담에도 수집 → 검색 → 답변이 그대로 돌아, 근거 문서에서 아무 사실이나 골라
"네. SK하이닉스는 ..."처럼 엉뚱하게 답했다. 근거만 쓰라는 규칙(P3)은 '무엇을 답할지'까지는 막지 못한다.
→ 수집 전에 범위를 판정하고, 범위 밖이면 수집도 답변도 하지 않는다.
"""

from langchain_core.prompts import ChatPromptTemplate

from finrag.domain import prompts
from finrag.domain.companies import mentioned_companies
from finrag.llm import chat


def check(company: str, question: str) -> prompts.TopicCheck:
    """실패하면 범위 안으로 본다 — 판정 오류로 정상 질문을 막는 쪽이 더 나쁘다."""
    if mentioned_companies(question):  # 목록 기업 이름이 나오면 LLM을 부르지 않는다
        return prompts.TopicCheck(on_topic=True, reason="기업 이름이 나온 질문")
    prompt = ChatPromptTemplate.from_messages([("system", prompts.TOPIC_SYSTEM), ("human", prompts.TOPIC_HUMAN)])
    try:
        return (prompt | chat("query").with_structured_output(prompts.TopicCheck)).invoke(
            {"company": company, "question": question}
        )
    except Exception:
        return prompts.TopicCheck(on_topic=True, reason="판정 실패")
