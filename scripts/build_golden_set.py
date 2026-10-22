"""골든셋 만들기 — 색인된 청크에서 질문 초안을 만들고 규칙으로 검수한다. → eval/golden_set.json

    python scripts/build_golden_set.py

1) 기업별로 실제 청크(기업정보 · 위키 기업 본문서 · DART 공시)와 용어집 청크를 고른다.
2) 상위 모델이 청크 하나로만 답할 수 있는 사실 질문 1개와 정답 키워드를 만든다.
3) 정답 키워드가 청크 원문에 그대로 있는지 코드로 확인하고, 아니면 버린다 (LLM 초안을 그대로 믿지 않는다).
4) 사람이 만든 질문(문서에 없는 질문 · 투자 판단 질문 · 두 가지를 묻는 질문)을 더한다.
각 문항은 정답 근거 문서(gold)를 갖는다 → Recall@k · MRR을 잴 수 있다.
"""

import _bootstrap  # noqa: F401

import json
import os
import random
import re

os.environ["MODEL_PROFILE"] = "final"  # 데이터셋은 한 번 만들고 오래 쓰므로 상위 모델로

from langchain_core.prompts import ChatPromptTemplate  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from finrag.config import ROOT  # noqa: E402
from finrag.domain.companies import item_by_code  # noqa: E402
from finrag.llm import chat  # noqa: E402
from finrag.store.vectorstore import collection  # noqa: E402

OUT = ROOT / "eval" / "golden_set.json"
COMPANIES = ["005930", "000660", "373220", "000270", "035420", "035720", "105560", "323410", "005380"]
PER_SOURCE = {"meta": 1, "wiki": 2, "dart": 2}
GLOSSARY_TERMS = 5
random.seed(20261022)


class Draft(BaseModel):
    answerable: bool = Field(description="이 청크만으로 구체적인 사실 질문을 만들 수 있으면 true")
    question: str = Field(description="기업명(또는 용어)을 넣은 자연스러운 한국어 질문 한 문장")
    answer_keywords: list[str] = Field(description="정답을 판정할 짧은 표현 1~3개. 청크 원문에 글자 그대로 있어야 함")


SYSTEM = """너는 RAG 평가용 질문을 만드는 사람이다. 주어진 [청크]만 읽고 답할 수 있는 사실 질문을 하나 만든다.
- 숫자, 날짜, 이름, 고유명사처럼 맞고 틀림을 판정할 수 있는 사실을 묻는다. 감상·전망·의견은 묻지 않는다.
- 질문에는 기업명(용어집이면 용어)을 넣어 혼자 읽어도 뜻이 통하게 한다. "이 문서", "위 내용" 같은 표현은 쓰지 않는다.
- answer_keywords는 청크 원문에 글자 그대로 들어 있는 짧은 표현(15자 이내)이다. 띄어쓰기도 원문과 같게 쓴다.
- 표의 칸 구분 기호나 목차만 있는 청크처럼 좋은 질문을 만들 수 없으면 answerable=false로 한다."""
HUMAN = "[대상] {target}\n[청크]\n{chunk}"


def squash(s: str) -> str:
    return re.sub(r"\s+", "", s)


def pick_chunks() -> list[dict]:
    """평가에 쓸 청크를 고른다. 사실이 담길 가능성이 큰(숫자가 있는) 300자 이상 청크 위주."""
    picks = []
    docs = collection("docs")
    meta = collection("meta")
    for code in COMPANIES:
        item = item_by_code(code)
        m = meta.get(ids=[code], include=["documents", "metadatas"])
        picks.append({"chunk_id": code, "doc_key": f"meta:{code}", "source_type": "meta",
                      "title": m["metadatas"][0]["title"], "text": m["documents"][0], "stock_code": code})
        got = docs.get(where={"stock_code": code}, include=["documents", "metadatas"])
        for src in ("wiki", "dart"):
            pool = [
                (cid, t, md) for cid, t, md in zip(got["ids"], got["documents"], got["metadatas"])
                if md["source_type"] == src and len(t) > 300 and re.search(r"\d", t)
                and (src == "dart" or md["title"] == item.get("wiki_title"))  # 위키는 기업 본문서만
            ]
            for cid, t, md in random.sample(pool, min(PER_SOURCE[src], len(pool))):
                picks.append({"chunk_id": cid, "doc_key": f"{src}:{md['doc_id']}", "source_type": src,
                              "title": md["title"], "text": t, "stock_code": code})
    g = collection("glossary").get(include=["documents", "metadatas"])
    pool = [(cid, t, md) for cid, t, md in zip(g["ids"], g["documents"], g["metadatas"]) if cid.endswith(":0")]
    for cid, t, md in random.sample(pool, GLOSSARY_TERMS):
        picks.append({"chunk_id": cid, "doc_key": f"glossary:{md['doc_id']}", "source_type": "glossary",
                      "title": md["title"], "text": t, "stock_code": random.choice(COMPANIES)})
    return picks


HAND_WRITTEN = [
    {"stock_code": "035420", "question": "네이버의 2030년 매출 전망은 얼마야?", "type": "unknown"},
    {"stock_code": "000660", "question": "SK하이닉스 직원들의 평균 출근 시간은 몇 시야?", "type": "unknown"},
    {"stock_code": "323410", "question": "카카오뱅크 대표이사가 좋아하는 음식은?", "type": "unknown"},
    {"stock_code": "000270", "question": "기아 주가 오를 거라던데 지금 사야 돼?", "type": "refuse"},
    {"stock_code": "005930", "question": "삼성전자 목표주가 얼마로 보면 돼?", "type": "refuse"},
    {"stock_code": "105560", "question": "KB금융 배당 받으려면 지금 들어가는 게 맞아?", "type": "refuse"},
]


def main() -> None:
    prompt = ChatPromptTemplate.from_messages([("system", SYSTEM), ("human", HUMAN)])
    gen = prompt | chat("answer").with_structured_output(Draft)
    items, dropped = [], []
    for p in pick_chunks():
        target = p["title"] if p["source_type"] == "glossary" else item_by_code(p["stock_code"])["name"]
        try:
            d = gen.invoke({"target": target, "chunk": p["text"][:2000]})
        except Exception as e:
            dropped.append((p["chunk_id"], f"생성 실패 {type(e).__name__}"))
            continue
        kws = [k.strip() for k in d.answer_keywords if k.strip()]
        ok_kws = [k for k in kws if squash(k) in squash(p["text"])]
        if not d.answerable or not ok_kws:
            dropped.append((p["chunk_id"], "답할 수 없음" if not d.answerable else f"키워드 원문 불일치 {kws}"))
            continue
        items.append({
            "id": f"g{len(items) + 1:02d}", "type": "term" if p["source_type"] == "glossary" else "fact",
            "stock_code": p["stock_code"], "question": d.question.strip(),
            "answer_groups": [[k] for k in ok_kws[:2]],  # 그룹마다 하나 이상 포함되면 정답
            "gold": [{"doc_key": p["doc_key"], "chunk_id": p["chunk_id"], "title": p["title"],
                      "source_type": p["source_type"]}],
        })
        print(f"  + {items[-1]['id']} [{p['source_type']}] {d.question[:60]} → {ok_kws[:2]}")

    # 두 가지를 묻는 질문: 같은 기업의 서로 다른 출처 사실 두 개를 붙인다 (질문 분해 개선의 기준)
    by_code: dict[str, list[dict]] = {}
    for it in items:
        if it["type"] == "fact":
            by_code.setdefault(it["stock_code"], []).append(it)
    multi = []
    for code, group in by_code.items():
        srcs = {}
        for it in group:
            srcs.setdefault(it["gold"][0]["source_type"], it)
        if "wiki" in srcs and "dart" in srcs and len(multi) < 5:
            a, b = srcs["wiki"], srcs["dart"]
            multi.append({
                "id": f"m{len(multi) + 1:02d}", "type": "multi", "stock_code": code,
                "question": f"{a['question'].rstrip('?')}? 그리고 {b['question']}",
                "answer_groups": a["answer_groups"][:1] + b["answer_groups"][:1],
                "gold": a["gold"] + b["gold"],
            })

    hand = [{"id": f"h{i + 1:02d}", "answer_groups": [], "gold": [], **h} for i, h in enumerate(HAND_WRITTEN)]
    data = {"note": "청크 기반 LLM 초안(키워드 원문 일치 검수) + 두 가지 질문 + 사람이 쓴 질문",
            "items": items + multi + hand, "dropped": dropped}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n단일 {len(items)} · 두 가지 {len(multi)} · 사람 작성 {len(hand)} = {len(data['items'])}문항 (버림 {len(dropped)}) → {OUT}")


if __name__ == "__main__":
    main()
