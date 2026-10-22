"""GMS 연결 점검: 크레딧 · 채팅 · 임베딩을 차례로 확인한다. 키 값은 출력하지 않는다.

    python scripts/check_gms.py
"""

import _bootstrap  # noqa: F401

import requests

from finrag.config import MissingKeyError, env, mask_secrets, model_name
from finrag.llm import chat, embeddings


def credit() -> None:
    r = requests.get(
        "https://gms.ssafy.io/gmsapi/key-info",
        headers={"authorization": f"Bearer {env('GMS_KEY')}"}, timeout=20,
    )
    r.raise_for_status()
    info = r.json()
    print(f"  전체 {info['totalCredit']} · 사용 {info['usedCredit']} · 잔여 {info['remainCredit']} · 만료 {info['expiredDate']}")


def main() -> None:
    try:
        env("GMS_KEY")
    except MissingKeyError as e:
        raise SystemExit(str(e))

    steps = [
        ("1. 크레딧", credit),
        (f"2. 채팅 ({model_name('answer')})",
         lambda: print("  응답:", chat("answer").invoke("한 문장으로 자기소개해줘.").content[:80])),
        ("3. 임베딩",
         lambda: print(f"  벡터 차원: {len(embeddings().embed_query('삼성전자 반도체'))}")),
    ]
    failed = 0
    for label, step in steps:
        print(label)
        try:
            step()
        except Exception as e:  # 원인 파악용 — 키는 마스킹
            failed += 1
            print("  실패:", mask_secrets(f"{type(e).__name__}: {e}")[:300])
    print("\n모두 통과" if not failed else f"\n실패 {failed}건")


if __name__ == "__main__":
    main()
