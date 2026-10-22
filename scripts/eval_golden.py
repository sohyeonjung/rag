"""골든셋 평가 — 검색 지표와 답변 지표를 함께 잰다. 개선 전후를 같은 기준으로 비교하기 위한 스크립트.

    python scripts/eval_golden.py --label baseline
    python scripts/eval_golden.py --label mmr --search-type mmr
    python scripts/eval_golden.py --label top10 --top-k 10

결과: docs/eval/golden_{label}.md (요약 + 문항별), eval/results/{label}.json (원자료)

수집 단계는 돌리지 않고 현재 색인으로만 평가한다 → 검색·생성 개선의 효과만 분리해서 본다.

검색 지표
  gold_recall        : 정답 근거 문서가 검색 결과(근거 목록)에 들어온 비율 (문서 단위)
  MRR                : 첫 정답 근거 문서의 순위 역수 평균 (근거 번호 순서 = 순위)
  answer_in_context  : 정답 키워드가 검색된 근거 어딘가에 들어 있는 비율
                       (같은 사실이 여러 문서에 있을 수 있어 gold_recall보다 관대함)
답변 지표
  accuracy           : 정답 키워드가 모두 답변에 있는 비율 (사실·용어·두 가지 질문)
  cited_gold         : 답변이 정답 근거 문서를 실제로 인용한 비율
  refusal            : 문서에 없는 질문·투자 판단 질문에 지어내지 않고 모른다/답할 수 없다고 한 비율
"""

import _bootstrap  # noqa: F401

import argparse
import json
import os
import re
import time
from collections import defaultdict
from datetime import date

UNKNOWN = ("확인되지 않", "알 수 없", "답할 수 없", "제공할 수 없", "확인할 수 없", "정보가 없")
REFUSE = ("투자 판단", "투자 권유", "투자 조언", "투자 자문", "답할 수 없", "드릴 수 없")


DATE_YMD = re.compile(r"(\d{4})\s*(?:년|[-./])\s*(\d{1,2})\s*(?:월|[-./])\s*(\d{1,2})\s*일?")
DATE_YM = re.compile(r"(\d{4})\s*(?:년|[-./])\s*(\d{1,2})\s*월?")


def squash(s: str) -> str:
    """비교용 정규화: 공백 제거 · 소문자 · 날짜 표기 통일.
    '2022-01-27', '2022.01.27', '2022년 1월 27일'을 같은 것으로 본다 — 표기만 다른 정답을 오답으로 세던 문제(g11, m03)"""
    s = DATE_YMD.sub(lambda m: f"{m[1]}-{int(m[2])}-{int(m[3])}", s)
    s = DATE_YM.sub(lambda m: f"{m[1]}-{int(m[2])}", s)
    return re.sub(r"\s+", "", s).lower()


def doc_key(ctx_id: str) -> str:
    """근거 청크 ID → 문서 단위 키 (골든셋 gold.doc_key와 같은 형식)."""
    parts = ctx_id.split(":")
    if len(parts) == 1:
        return f"meta:{ctx_id}"
    if parts[0] == "term":
        return f"glossary:term{parts[1]}"
    return f"{parts[1]}:{parts[2]}"


def has_all(text: str, groups: list[list[str]]) -> bool:
    t = squash(text)
    return all(any(squash(k) in t for k in g) for g in groups)


def score(item: dict, result: dict) -> dict:
    ctx = result["contexts"]
    keys = [doc_key(c["id"]) for c in ctx]
    gold = {g["doc_key"] for g in item["gold"]}
    row = {"id": item["id"], "type": item["type"], "question": item["question"],
           "answer": result["answer"], "contexts": len(ctx),
           "cited": [f"{c['n']}:{c['title']}" for c in result["cited"]]}
    if gold:
        found = gold & set(keys)
        row["gold_recall"] = len(found) / len(gold)
        # 청크 단위: 질문을 만든 바로 그 청크가 들어왔는가 (문서 단위는 기업 본문서·공시가 항상 들어와 관대함)
        gold_chunks = {g["chunk_id"] for g in item["gold"]}
        row["chunk_recall"] = len(gold_chunks & {c["id"] for c in ctx}) / len(gold_chunks)
        ranks = [i for i, k in enumerate(keys, start=1) if k in gold]
        row["rr"] = 1 / ranks[0] if ranks else 0.0
        row["answer_in_context"] = has_all("\n".join(c["text"] for c in ctx), item["answer_groups"])
        row["accuracy"] = has_all(result["answer"], item["answer_groups"])
        row["cited_gold"] = bool({doc_key(c["id"]) for c in result["cited"]} & gold)
    else:
        phrases = UNKNOWN if item["type"] == "unknown" else REFUSE + UNKNOWN
        admits = any(p in result["answer"] for p in phrases)
        invented = item["type"] == "unknown" and not admits and re.search(r"\d+\s*(조|억|원|%|시)", result["answer"])
        row["refusal"] = admits and not invented
    return row


def mean(rows: list[dict], key: str) -> float | None:
    vals = [float(r[key]) for r in rows if key in r]
    return sum(vals) / len(vals) if vals else None


def fmt(v: float | None) -> str:
    return "-" if v is None else f"{v:.3f}"


def main(label: str, search_type: str | None, top_k: int | None, profile: str, decompose: bool | None,
         hybrid: bool | None) -> None:
    os.environ["MODEL_PROFILE"] = profile
    from finrag.config import ROOT, model_name, settings
    from finrag.domain.companies import item_by_code
    from finrag.rag.graph import ask

    golden = json.loads((ROOT / "eval" / "golden_set.json").read_text(encoding="utf-8"))["items"]
    conf = settings()["retrieval"]
    rows, t0 = [], time.perf_counter()
    for item in golden:
        name = item_by_code(item["stock_code"])["name"]
        task = "term" if item["type"] == "term" else "qa"
        overrides = {k: v for k, v in (("search_type", search_type), ("top_k", top_k)) if v}
        if decompose is not None:
            overrides["decompose"] = decompose
        if hybrid is not None:
            overrides["hybrid"] = hybrid
        result = ask(item["stock_code"], name, item["question"], task=task, **overrides)
        rows.append(score(item, result))
        r = rows[-1]
        print(f"  {r['id']} {r['type']:<7} recall={r.get('gold_recall', '-')} acc={r.get('accuracy', r.get('refusal'))}")
    elapsed = time.perf_counter() - t0
    meta = {"label": label, "date": date.today().isoformat(), "model": model_name("answer"),
            "search_type": search_type or conf["search_type"], "top_k": top_k or conf["top_k"],
            "decompose": conf.get("decompose", False) if decompose is None else decompose,
            "hybrid": conf.get("hybrid", False) if hybrid is None else hybrid, "seconds": round(elapsed, 1)}
    write_report(rows, meta)


def write_report(rows: list[dict], meta: dict) -> None:
    from finrag.config import ROOT

    label = meta["label"]
    by_type = defaultdict(list)
    for r in rows:
        by_type[r["type"]].append(r)
    summary = {
        **meta, "n": len(rows),
        "gold_recall": mean(rows, "gold_recall"), "chunk_recall": mean(rows, "chunk_recall"), "mrr": mean(rows, "rr"),
        "answer_in_context": mean(rows, "answer_in_context"), "accuracy": mean(rows, "accuracy"),
        "cited_gold": mean(rows, "cited_gold"), "refusal": mean(rows, "refusal"),
        "by_type": {t: {k: mean(rs, k) for k in ("gold_recall", "chunk_recall", "accuracy", "refusal")} | {"n": len(rs)}
                    for t, rs in by_type.items()},
    }
    for t, rs in by_type.items():
        summary["by_type"][t]["mrr"] = mean(rs, "rr")

    (ROOT / "eval" / "results").mkdir(parents=True, exist_ok=True)
    (ROOT / "eval" / "results" / f"{label}.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")

    s = summary
    lines = [
        f"# 골든셋 평가 — {label}", "",
        f"- 실행일 {s['date']} · 답변 모델 `{s['model']}` · 검색 {s['search_type']} · Top-k {s['top_k']} · 질문 분해 {'켬' if s['decompose'] else '끔'} · 하이브리드 {'켬' if s.get('hybrid') else '끔'} · {s['n']}문항 · {s['seconds']}초",
        "- 골든셋: `eval/golden_set.json` (청크 기반 초안 + 원문 일치 검수 + 사람 검수) · 수집 없이 현재 색인으로 평가", "",
        "| 지표 | 값 |", "| --- | --- |",
        f"| gold_recall (정답 근거 문서 검색률) | {fmt(s['gold_recall'])} |",
        f"| chunk_recall (질문을 만든 청크 검색률) | {fmt(s['chunk_recall'])} |",
        f"| MRR | {fmt(s['mrr'])} |",
        f"| answer_in_context (정답이 근거 안에 있음) | {fmt(s['answer_in_context'])} |",
        f"| accuracy (답변 정답률) | {fmt(s['accuracy'])} |",
        f"| cited_gold (정답 근거 인용률) | {fmt(s['cited_gold'])} |",
        f"| refusal (모름·거절 정확도) | {fmt(s['refusal'])} |", "",
        "## 유형별", "", "| 유형 | 문항 | gold_recall | chunk_recall | MRR | accuracy | refusal |", "| --- | --- | --- | --- | --- | --- | --- |",
        *[f"| {t} | {v['n']} | {fmt(v['gold_recall'])} | {fmt(v['chunk_recall'])} | {fmt(v['mrr'])} | {fmt(v['accuracy'])} | {fmt(v['refusal'])} |"
          for t, v in s["by_type"].items()], "",
        "## 틀린 문항", "",
        *[f"- **{r['id']}** ({r['type']}) {r['question']} — recall {r.get('gold_recall', '-')}, "
          f"근거 안 정답 {r.get('answer_in_context', '-')} · 답변: {' '.join(r['answer'].split())[:120]}"
          for r in rows if r.get("accuracy") is False or r.get("refusal") is False],
    ]
    out = ROOT / "docs" / "eval" / f"golden_{label}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nrecall {fmt(s['gold_recall'])} · MRR {fmt(s['mrr'])} · 정답 {fmt(s['accuracy'])} · 거절 {fmt(s['refusal'])} → {out}")


def rescore(label: str) -> None:
    """저장된 답변으로 정답 판정만 다시 한다 (API 호출 없음). 채점 규칙을 고쳤을 때 쓴다."""
    from finrag.config import ROOT

    saved = json.loads((ROOT / "eval" / "results" / f"{label}.json").read_text(encoding="utf-8"))
    golden = {i["id"]: i for i in json.loads((ROOT / "eval" / "golden_set.json").read_text(encoding="utf-8"))["items"]}
    rows = saved["rows"]
    for r in rows:
        if "accuracy" in r:
            r["accuracy"] = has_all(r["answer"], golden[r["id"]]["answer_groups"])
    meta = {k: saved["summary"][k] for k in ("label", "date", "model", "search_type", "top_k", "seconds")}
    meta["decompose"] = saved["summary"].get("decompose", False)
    meta["hybrid"] = saved["summary"].get("hybrid", False)
    meta["rescored"] = date.today().isoformat()
    write_report(rows, meta)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--label", required=True)
    p.add_argument("--search-type", choices=["similarity", "mmr"])
    p.add_argument("--top-k", type=int)
    p.add_argument("--profile", default="dev", choices=["dev", "final"])
    p.add_argument("--decompose", action=argparse.BooleanOptionalAction, default=None, help="질문 분해 (기본: config.yaml)")
    p.add_argument("--hybrid", action=argparse.BooleanOptionalAction, default=None, help="하이브리드 검색 (기본: config.yaml)")
    p.add_argument("--rescore", action="store_true", help="API 호출 없이 저장된 답변으로 다시 채점")
    a = p.parse_args()
    if a.rescore:
        rescore(a.label)
    else:
        main(a.label, a.search_type, a.top_k, a.profile, a.decompose, a.hybrid)
