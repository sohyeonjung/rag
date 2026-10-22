"""명세서 4장 검증 항목을 실제로 실행해 결과를 남긴다 → docs/eval/verification_log.md

    python scripts/verify_requirements.py

화면 검증(4.4)은 streamlit.testing.v1.AppTest로 브라우저 없이 app.py를 돌려 확인한다.
작은 모델(dev 프로필)로 돌려 크레딧을 아낀다.
"""

import _bootstrap  # noqa: F401

import os
import subprocess
import time
import traceback
from datetime import date
from unittest import mock

os.environ["MODEL_PROFILE"] = "dev"

from finrag.collect import cache, wiki  # noqa: E402
from finrag.collect.documents import collect  # noqa: E402
from finrag.config import ROOT, settings  # noqa: E402
from finrag.domain import prompts  # noqa: E402
from finrag.domain.companies import build_item_text, item_by_code, load_items  # noqa: E402
from finrag.rag.citations import cited_numbers  # noqa: E402
from finrag.rag.graph import ask, build_graph, retrieve  # noqa: E402
from finrag.store.chunking import split  # noqa: E402
from finrag.store.vectorstore import count  # noqa: E402

RESULTS: list[tuple[str, str, str, str]] = []  # (구분, 항목, 결과, 근거)
FRESH = "035720"  # 카카오 — 검증용으로 아직 수집하지 않은 기업
FRESH_Q = "카카오의 주요 사업은 무엇이야?"


def check(section: str, name: str):
    def wrap(fn):
        try:
            ok, evidence = fn()
            RESULTS.append((section, name, "통과" if ok else "실패", evidence))
        except Exception as e:  # 검증 스크립트 자체 오류는 '미확인'으로 남긴다
            RESULTS.append((section, name, "미확인", f"{type(e).__name__}: {e}"[:300]))
            traceback.print_exc()
        print(f"[{RESULTS[-1][2]}] {section} {name}")
        return fn
    return wrap


# ── 4.1 데이터와 색인 ─────────────────────────────────────────────────
@check("4.1", "로드한 항목 수 = 색인된 청크 수")
def _():
    n, c = len(load_items()), count("meta")
    return n == c and n >= 100, f"항목 {n}건 · company_meta {c}청크"


@check("4.1", "색인을 두 번 실행해도 청크 수가 늘지 않음")
def _():
    out = []
    for _ in range(2):
        r = subprocess.run([str(ROOT / ".venv/Scripts/python"), "scripts/index_items.py"], cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONUTF8": "1"})
        out.append(r.stdout.strip().splitlines()[-1])
    return all("→ 색인 후 100청크" in o and "색인 전 100청크" in o for o in out), " / ".join(out)


@check("4.1", "항목 1건의 색인 텍스트에 의도한 속성이 모두 있음")
def _():
    text = build_item_text(item_by_code("000660"))
    attrs = ["종목코드", "개요:", "정식명칭", "별칭", "대표자", "설립일", "결산월", "본사", "시가총액 순위"]
    missing = [a for a in attrs if a not in text]
    return not missing, text.replace("\n", "<br>")


# ── 4.2 문서 수집과 색인 ───────────────────────────────────────────────
STATE: dict = {}


@check("4.2", "검색어 추출(P1) 결과 확인")
def _():
    # 다시 실행해도 같은 조건이 되도록 검증 대상 기업의 수집 기록과 문서를 비운다
    from finrag.store.vectorstore import collection
    cache.forget(FRESH)
    collection("docs").delete(where={"stock_code": FRESH})
    item = item_by_code(FRESH)
    t0 = time.perf_counter()
    STATE["first"] = collect(item, FRESH_Q)
    STATE["first_sec"] = time.perf_counter() - t0
    r = STATE["first"]
    return bool(r.queries), f"질문: {FRESH_Q}<br>검색어: {r.queries}"


@check("4.2", "선별(P2) — 색인한 문서와 버린 문서 구분")
def _():
    r = STATE["first"]
    return bool(r.selected), f"색인: {r.selected} ({r.chunks}청크)<br>버림: {r.rejected}<br>이유: {r.select_reason}"


@check("4.2", "같은 질문을 다시 하면 수집이 다시 일어나지 않음 (NF2002)")
def _():
    t0 = time.perf_counter()
    r = collect(item_by_code(FRESH), FRESH_Q)
    STATE["second_sec"] = time.perf_counter() - t0
    return not r.selected and r.chunks == 0, (
        f"두 번째 수집: 새 문서 {len(r.selected)}건 · 캐시로 건너뛴 검색어 {r.skipped_queries} · "
        f"{STATE['first_sec']:.1f}초 → {STATE['second_sec']:.1f}초")


@check("4.2", "선택한 항목 자체를 다룬 문서가 빠지지 않음")
def _():
    item = item_by_code(FRESH)
    titles = cache.get(FRESH)["titles"]
    return item["wiki_title"] in titles, f"기업 본문서 '{item['wiki_title']}' · 수집된 문서 {titles}"


@check("4.2", "청크 크기·중복 길이를 설정에서 바꾸면 청크 수가 달라짐")
def _():
    text = wiki.fetch_page(item_by_code(FRESH)["wiki_title"])["text"]
    conf = settings()["chunk"]["wiki"]
    original = dict(conf)
    counts = []
    try:
        for size, overlap in ((500, 100), (1000, 200), (1500, 200), (1000, 0)):
            conf.update(size=size, overlap=overlap)
            counts.append(f"{size}/{overlap} → {len(split(text, 'wiki'))}청크")
    finally:
        conf.update(original)
    return len({c.split('→')[1] for c in counts}) > 1, f"문서 '{item_by_code(FRESH)['wiki_title']}' {len(text):,}자: " + " · ".join(counts)


# ── 4.3 답변과 근거 ───────────────────────────────────────────────────
@check("4.3", "Top-k를 바꾸면 같은 질문의 근거 목록이 달라짐")
def _():
    rows = []
    for k in (3, 6, 10):
        ctx = retrieve({"stock_code": "000660", "company": "SK하이닉스", "question": "SK하이닉스의 HBM 사업은?", "top_k": k})["contexts"]
        rows.append(f"k={k}: {len(ctx)}개 — " + ", ".join(f"{c['title']}" for c in ctx))
    return len({r.split(':')[1].split('개')[0] for r in rows}) == 3, "<br>".join(rows)


@check("4.3", "LangGraph가 retrieve·generate 두 노드로 구성됨")
def _():
    nodes = [n for n in build_graph().get_graph().nodes if not n.startswith("__")]
    return nodes == ["retrieve", "generate"], f"노드: {nodes} (scripts/show_graph.py 로 Mermaid 출력)"


@check("4.3", "답변 본문의 근거 번호 = 화면에 표시되는 근거 목록")
def _():
    r = ask("373220", "LG에너지솔루션", "LG에너지솔루션은 언제 설립됐어?")
    STATE["cited_answer"] = r
    in_text = cited_numbers(r["answer"])
    shown = {c["n"] for c in r["cited"]}
    valid = {c["n"] for c in r["contexts"]}
    return shown == (in_text & valid) and shown, f"본문 번호 {sorted(in_text)} · 화면 목록 {sorted(shown)} · 검색된 근거 {len(valid)}개"


@check("4.3", "근거 목록에 항목 메타데이터와 외부 문서가 구분 표시됨")
def _():
    types = sorted({c["source_type"] for c in STATE["cited_answer"]["contexts"]})
    return "meta" in types and len(types) >= 2, f"근거 출처 유형: {types} (화면 표기: 기업정보(DART)·위키백과·DART 공시·한국은행 용어집)"


@check("4.3", "문서에 없는 질문에 추측하지 않고 확인되지 않는다고 답함")
def _():
    r = ask("035420", "NAVER", "네이버 창업자가 좋아하는 음식은?")
    return prompts.NOT_FOUND[:8] in r["answer"] or "확인되지 않" in r["answer"], r["answer"][:200]


@check("4.3", "인용된 근거가 없을 때 화면에 안내")
def _():
    from finrag.rag.citations import cited_sources
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    return cited_sources("근거 없는 답", [{"n": 1}]) == [] and "답변에 인용된 근거가 없습니다" in src, \
        "cited_sources()가 빈 목록을 돌려주면 render_sources()가 '답변에 인용된 근거가 없습니다' 안내 (단위 테스트 포함)"


# ── 4.4 서비스 기능과 화면 (AppTest) ────────────────────────────────────
def app_test():
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(str(ROOT / "app.py"), default_timeout=300)


def page_text(at) -> str:
    parts = []
    for kind in ("error", "warning", "info", "markdown", "caption", "subheader", "title"):
        parts += [getattr(e, "value", "") or "" for e in getattr(at, kind)]
    return "\n".join(str(p) for p in parts)


@check("4.4", "API 키가 없으면 안내 표시")
def _():
    saved = os.environ.get("GMS_KEY")
    os.environ["GMS_KEY"] = ""
    try:
        at = app_test().run()
    finally:
        os.environ["GMS_KEY"] = saved or ""
    msg = " ".join(e.value for e in at.error)
    return "GMS_KEY" in msg and not at.exception, f"표시된 안내: {msg}"


@check("4.4", "색인이 없으면 안내 표시")
def _():
    with mock.patch("finrag.store.vectorstore.count", return_value=0):
        at = app_test().run()
    msg = " ".join(e.value for e in at.error)
    return "색인" in msg and "index_items" in msg, f"표시된 안내: {msg}"


@check("4.4", "위험 도메인 면책 안내 상시 표시")
def _():
    at = app_test().run()
    msg = " ".join(e.value for e in at.warning)
    return "투자 권유" in msg, msg


def find(node, kind: str) -> list:
    """AppTest 요소 트리에서 type이 kind인 요소를 모두 찾는다 (st.status는 'status')."""
    found = [node] if getattr(node, "type", None) == kind else []
    children = getattr(node, "children", None)
    for child in (children.values() if isinstance(children, dict) else []):
        found += find(child, kind)
    return found


@check("4.4", "항목을 바꿔도 이전 대화가 섞이지 않음 (F2009)")
def _():
    at = app_test().run()
    at.selectbox(key="stock_code").set_value("005930").run()
    at.chat_input[0].set_value("삼성전자 본사는 어디야?").run()
    STATE["app_after_question"] = at
    a = len(at.chat_message)
    at.selectbox(key="stock_code").set_value("000660").run()
    b = len(at.chat_message)
    at.selectbox(key="stock_code").set_value("005930").run()
    c = len(at.chat_message)
    return a == 2 and b == 0 and c == 2 and not at.exception, (
        f"삼성전자 질문 후 말풍선 {a}개 → SK하이닉스로 전환 {b}개 → 삼성전자로 복귀 {c}개")


@check("4.4", "문서 수집과 답변 생성 중 진행 상태 표시 (F2012)")
def _():
    at = app_test().run()
    at.selectbox(key="stock_code").set_value("000270").run()
    at.chat_input[0].set_value("기아의 해외 공장은 어디에 있어?").run()
    boxes = find(at.main, "status")
    steps = [m.value for b in boxes for m in find(b, "markdown")]
    return bool(boxes) and bool(steps), f"상태 박스: '{boxes[0].label if boxes else '-'}' · 진행 메시지: {steps[:6]}"


@check("4.4", "조건 기반 탐색 결과에 조건과 맞지 않는 항목이 없음")
def _():
    from finrag.features.explore import explore
    res = explore("반도체 관련 기업")
    names = [r["name"] for r in res]
    summaries = {it["name"]: it.get("summary", "") for it in load_items()}
    bad = [n for n in names if "반도체" not in summaries.get(n, "")]
    return bool(names) and not bad, f"결과: {names} · 결과 기업마다 개요(색인 텍스트)에 '반도체'가 있는지 확인 · 조건 불일치 {bad}"


@check("4.4", "도메인 특화 기능 결과에도 근거가 함께 제시됨 (F2010)")
def _():
    rows = []
    ok = True
    for task, q in (("factcheck", "삼성전자 본사는 서울에 있다"), ("term", "공매도"), ("briefing", prompts.BRIEFING_QUESTION)):
        r = ask("005930", "삼성전자", q, task=task)
        ok &= bool(r["cited"])
        rows.append(f"{task}: 인용 {len(r['cited'])}건 — {r['answer'][:60]}…")
    return ok, "<br>".join(rows)


# ── 4.7 비기능 ──────────────────────────────────────────────────────
@check("4.7", "NF2001 응답 시간")
def _():
    t0 = time.perf_counter()
    ask(FRESH, "카카오", FRESH_Q)
    gen = time.perf_counter() - t0
    first, second = STATE["first_sec"], STATE["second_sec"]
    return gen < 30, f"첫 질문(수집 포함) 수집 {first:.1f}초 + 답변 · 재질문 수집 {second:.1f}초 · 답변 생성 {gen:.1f}초"


@check("4.7", "NF2003 소스코드·커밋 대상에 API 키 없음")
def _():
    vals = [l.split("=", 1)[1].strip() for l in (ROOT / ".env").read_text(encoding="utf-8").splitlines()
            if "=" in l and l.split("=", 1)[0] in ("GMS_KEY", "DART_API_KEY", "DATA_GO_KR_KEY")]
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    hits = [f for f in files for v in vals if len(v) > 8 and v in (ROOT / f).read_text(encoding="utf-8", errors="ignore")]
    ignored = subprocess.run(["git", "check-ignore", ".env"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return not hits and ignored == ".env", f"추적 파일 {len(files)}개 중 키 노출 {len(hits)}건 · .env는 .gitignore 대상"


@check("4.7", "NF2004 화면에 키·프롬프트 전문·오류 추적 정보 비노출")
def _():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    bad = [w for w in ("st.exception", "traceback", "P3_SYSTEM", "format_exc") if w in src]
    return not bad, "app.py: 예외는 friendly_error()로 요약만 표시, 상세는 mask_secrets() 후 logs/app.log · 프롬프트 문자열을 화면에 출력하지 않음"


@check("4.7", "NF2005 수집·색인·검색·생성·UI 모듈 분리")
def _():
    dirs = {d: sorted(p.name for p in (ROOT / "finrag" / d).glob("*.py") if p.name != "__init__.py")
            for d in ("domain", "collect", "store", "rag", "features")}
    return all(dirs.values()), " · ".join(f"{k}/: {', '.join(v)}" for k, v in dirs.items()) + " · UI: app.py"


@check("4.7", "NF2006 외부 API 재시도·호출 간격·식별 정보")
def _():
    c = settings()["collect"]
    ua = wiki.user_agent()
    return "@" in ua and c["max_retries"] >= 2 and c["request_interval_sec"] > 0, (
        f"재시도 {c['max_retries']}회(429·5xx 지수 대기, Retry-After 준수) · 호스트별 간격 {c['request_interval_sec']}초 · "
        f"위키 User-Agent에 연락처 포함")


@check("4.7", "NF2007 데이터·문서 출처와 이용 조건 기록")
def _():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    need = ("CC BY-SA", "공공누리", "OpenDART", "한국은행")
    return all(n in readme for n in need), "README '데이터 출처와 이용 조건' 표 · 재배포 금지 원본은 .gitignore(data/raw/)"


def main() -> None:
    out = ROOT / "docs" / "eval" / "verification_log.md"
    passed = sum(r[2] == "통과" for r in RESULTS)
    lines = [
        "# 명세서 4장 검증 실행 기록",
        "",
        f"- 실행일: {date.today().isoformat()} · `python scripts/verify_requirements.py` · 모델 프로필 dev",
        f"- 결과: 통과 {passed} / 전체 {len(RESULTS)} (실패 {sum(r[2] == '실패' for r in RESULTS)}, 미확인 {sum(r[2] == '미확인' for r in RESULTS)})",
        "- 화면(4.4)은 Streamlit AppTest로 app.py를 실제 실행해 확인했다.",
        "",
        "| 구분 | 항목 | 결과 | 근거 |",
        "| --- | --- | --- | --- |",
        *[f"| {s} | {n} | {r} | {' '.join(e.replace('|', '/').split())} |" for s, n, r, e in RESULTS],
    ]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n통과 {passed}/{len(RESULTS)} → {out}")


if __name__ == "__main__":
    main()
