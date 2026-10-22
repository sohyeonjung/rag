"""F2015 — RAG 사용 전후 답변 비교. 같은 질문 · 같은 모델로 한 번은 LLM만, 한 번은 RAG로 답하게 한다.

    python scripts/eval_rag.py                  # final 프로필(답변 생성 상위 모델)로 실행 → docs/eval/rag_comparison.md
    python scripts/eval_rag.py --profile dev    # 작은 모델로 빠르게 확인

비교 기준 (4.6)
  사실 정확성   : 정답 키워드가 답변에 있는가 (문서로 확인한 정답 — 아래 CASES의 출처)
  근거 제시     : 확인할 수 있는 출처를 댔는가 (RAG: 실제 인용된 근거 수 / LLM만: 출처 언급 여부)
  모름 인정     : 답이 없거나 답하면 안 되는 질문에서 지어내지 않고 모른다/답할 수 없다고 했는가
"""

import _bootstrap  # noqa: F401

import argparse
import os
import re
from datetime import date

CASES = [
    # (종목코드, 질문, 유형, 정답 키워드 그룹(그룹마다 하나 이상 포함), 정답 출처)
    ("373220", "LG에너지솔루션은 언제 LG화학에서 분할돼 설립됐나?", "fact", [["2020"], ["12월"]], "DART 기업개황 설립일 2020-12-01 · 위키"),
    ("000660", "SK하이닉스는 언제, 어떤 이름의 회사로 처음 설립됐나?", "fact", [["1949"], ["국도건설"]], "위키 연혁 · DART 설립일 1949-10-15"),
    ("005930", "삼성전자 본사는 어느 도시에 있어?", "fact", [["수원"]], "DART 기업개황 주소"),
    ("005930", "삼성전자의 2026년 반기 DS 부문 매출은 얼마야?", "fact", [["209조", "2,092,317"]], "DART 반기보고서(2026.06)"),
    ("000270", "기아의 현재 대표이사는 누구야?", "fact", [["송호성"], ["송민수"]], "DART 기업개황 대표자"),
    ("105560", "KB금융지주는 언제 설립됐어?", "fact", [["2008"]], "DART 기업개황 설립일 2008-09-29"),
    ("373220", "물적분할이 뭐야?", "term", [["모회사"]], "한국은행 경제금융용어 800선 p.74"),
    ("035420", "네이버의 2030년 매출 전망은 얼마야?", "unknown", [], "어느 문서에도 없음 → 모른다고 해야 정답"),
    ("000270", "기아 주가 오를 거라던데 지금 사야 돼?", "refuse", [], "투자 판단 → 거절해야 정답"),
]

UNKNOWN_PHRASES = ("확인되지 않", "알 수 없", "답할 수 없", "제공할 수 없", "예측할 수 없", "드릴 수 없",
                   "확인할 수 없", "어렵", "공개되지 않", "정보가 없")
REFUSE_PHRASES = ("투자 판단", "투자 권유", "투자 조언", "투자 자문", "제공하지 않", "답할 수 없", "드릴 수 없", "권유하지")
SOURCE_HINT = re.compile(r"https?://|출처|DART|사업보고서|위키백과|\[근거")

BASELINE_SYSTEM = "너는 기업 정보 안내원이다. 한국어로 간결하게 답한다."


def score(kind: str, answer: str, groups: list[list[str]]) -> str:
    if kind in ("fact", "term"):
        hit = sum(any(k in answer for k in g) for g in groups)
        if hit == len(groups):
            return "정확"
        if hit:
            return "부분"
        # 틀린 값을 지어낸 것과 모른다고 한 것은 다르다 — 후자는 환각이 아니다
        return "모름(답 못함)" if any(p in answer for p in UNKNOWN_PHRASES) else "오답"
    phrases = UNKNOWN_PHRASES if kind == "unknown" else REFUSE_PHRASES + UNKNOWN_PHRASES
    admits = any(p in answer for p in phrases)
    invented = kind == "unknown" and re.search(r"\d+\s*(조|억|원|%)", answer) and not admits
    return "정확(모름 인정)" if admits and not invented else "오답(지어냄)"


def main(profile: str) -> None:
    os.environ["MODEL_PROFILE"] = profile  # chat()이 처음 만들어지기 전에 정한다
    from langchain_core.prompts import ChatPromptTemplate

    from finrag.collect.documents import collect
    from finrag.config import ROOT, model_name
    from finrag.domain.companies import item_by_code
    from finrag.llm import chat
    from finrag.rag.graph import ask

    baseline = ChatPromptTemplate.from_messages([("system", BASELINE_SYSTEM), ("human", "[기업] {company}\n[질문] {question}")])
    rows, details = [], []
    for code, question, kind, groups, truth in CASES:
        item = item_by_code(code)
        print(f"- {item['name']}: {question}")
        plain = (baseline | chat("answer")).invoke({"company": item["name"], "question": question}).content

        collect(item, question)
        result = ask(code, item["name"], question, task="term" if kind == "term" else "qa")
        rag, cited = result["answer"], result["cited"]

        s_plain, s_rag = score(kind, plain, groups), score(kind, rag, groups)
        src_plain = "언급만(확인 불가)" if SOURCE_HINT.search(plain) else "없음"
        src_rag = f"{len(cited)}건 (" + ", ".join(sorted({c['source_type'] for c in cited})) + ")" if cited else "없음"
        rows.append((item["name"], question, kind, s_plain, s_rag, src_plain, src_rag))
        details += [f"### {item['name']} — {question}", f"- 유형: {kind} · 정답 근거: {truth}", "",
                    f"**LLM만** ({s_plain})", "", "> " + plain.replace("\n", "\n> "), "",
                    f"**RAG** ({s_rag}, 인용 {src_rag})", "", "> " + rag.replace("\n", "\n> "), ""]
        if cited:
            details += ["인용된 근거: " + " / ".join(f"[근거 {c['n']}] {c['title']}" for c in cited), ""]

    def rate(col: int) -> str:
        ok = sum(r[col].startswith("정확") for r in rows)
        return f"{ok}/{len(rows)}"

    def wrong(col: int) -> str:
        return f"{sum(r[col].startswith('오답') for r in rows)}건"

    out = ROOT / "docs" / "eval" / "rag_comparison.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# F2015 RAG 사용 전후 답변 비교", "",
        f"- 실행일: {date.today().isoformat()} · 답변 모델: `{model_name('answer')}` (두 방식 동일) · 프로필: {profile}",
        "- 정답은 DART 기업개황·정기보고서, 위키백과, 한국은행 용어집에서 미리 확인한 값이다.",
        "- 채점: 정답 키워드 포함 여부(자동). 아래 원문 답변으로 사람이 다시 확인할 수 있다.", "",
        "## 요약", "",
        "| 기준 | LLM만 | RAG |", "| --- | --- | --- |",
        f"| 사실 정확성 · 모름 인정 (정답 수) | {rate(3)} | {rate(4)} |",
        f"| 틀린 내용을 지어냄 (오답) | {wrong(3)} | {wrong(4)} |",
        f"| 확인 가능한 근거 제시 | 0/{len(rows)} | {sum(r[6] != '없음' for r in rows)}/{len(rows)} |", "",
        "## 질문별 결과", "",
        "| 기업 | 질문 | 유형 | LLM만 | RAG | LLM만 출처 | RAG 인용 근거 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
        *[f"| {' | '.join(r)} |" for r in rows], "",
        "## 답변 원문", "", *details,
    ]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nLLM만 {rate(3)} · RAG {rate(4)} → {out}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--profile", default="final", choices=["dev", "final"])
    main(p.parse_args().profile)
