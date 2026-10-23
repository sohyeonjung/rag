"""공시 재색인 뒤 골든셋의 공시 정답 청크 ID를 새 청크에 다시 맞춘다.

    python scripts/remap_golden_dart.py --save   # 재색인 전: 지금 정답 청크 본문을 저장
    python scripts/remap_golden_dart.py          # 재색인 후: 같은 공시의 새 청크 중 정답 키워드가 있고
                                                 #             옛 본문과 가장 많이 겹치는 청크로 바꾼다

질문·정답 키워드는 그대로 두고 '어느 청크가 정답인가'만 다시 가리킨다. 옛 ID는 chunk_id_prev에 남긴다.
"""

import _bootstrap  # noqa: F401

import argparse
import json

from finrag.config import ROOT
from finrag.rag.keyword import tokenize
from finrag.store.vectorstore import collection

GOLDEN = ROOT / "eval" / "golden_set.json"
SNAPSHOT = ROOT / "eval" / "golden_dart_chunks_prev.json"


def dart_golds(data: dict):
    for item in data["items"]:
        for g in item["gold"]:
            if g["source_type"] == "dart":
                yield item, g


def save() -> None:
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    ids = list(dict.fromkeys(g["chunk_id"] for _, g in dart_golds(data)))  # 두 가지 질문은 같은 청크를 다시 쓴다
    got = collection("docs").get(ids=ids, include=["documents"])
    SNAPSHOT.write_text(json.dumps(dict(zip(got["ids"], got["documents"])), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"정답 청크 {len(got['ids'])}/{len(ids)}개 본문 저장 → {SNAPSHOT}")


def remap() -> None:
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    prev = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    docs = collection("docs")
    for item, g in dart_golds(data):
        old_id = g.get("chunk_id_prev") or g["chunk_id"]
        code, _, rcept, _ = old_id.split(":")
        got = docs.get(where={"$and": [{"stock_code": code}, {"doc_id": rcept}]}, include=["documents"])
        keys = [k for group in item["answer_groups"] for k in group]
        old_tokens = set(tokenize(prev[old_id]))
        scored = [(len(old_tokens & set(tokenize(t))), cid) for cid, t in zip(got["ids"], got["documents"])
                  if any(k.replace(" ", "") in t.replace(" ", "") for k in keys)]
        if not scored:
            print(f"  {item['id']}: 정답 키워드가 있는 새 청크 없음 — 그대로 둠")
            continue
        new_id = max(scored)[1]
        g["chunk_id_prev"], g["chunk_id"] = old_id, new_id
        print(f"  {item['id']}: {old_id} → {new_id}")
    GOLDEN.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--save", action="store_true")
    save() if p.parse_args().save else remap()
