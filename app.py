"""FinRAG — 근거로 답하는 상장기업 리서치 (Streamlit). 교육생 배포 step7_skeleton.py 골격에서 출발했다.

    streamlit run app.py
"""

import logging

import streamlit as st

from finrag.collect.documents import collect
from finrag.collect.http import ExternalAPIError
from finrag.collect import cache
from finrag.config import ROOT, MissingKeyError, env, mask_secrets, settings
from finrag.domain import prompts
from finrag.domain.companies import ITEM_LABEL, display_name, item_by_code, load_items
from finrag.features.explore import explore
from finrag.rag.citations import SOURCE_LABELS
from finrag.rag.graph import ask
from finrag.store.vectorstore import count

(ROOT / "logs").mkdir(exist_ok=True)
logging.basicConfig(filename=ROOT / "logs" / "app.log", level=logging.INFO, encoding="utf-8",
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("finrag")

MODES = {"질문하기": "qa", "브리핑 카드": "briefing", "팩트체크": "factcheck"}
INPUT_HINT = {
    "qa": "이 기업에 대해 궁금한 점을 물어보세요",
    "briefing": "브리핑에서 특히 보고 싶은 내용이 있으면 적어 주세요 (비우고 아래 버튼을 눌러도 됩니다)",
    "factcheck": "검증할 주장을 붙여 넣으세요. 예) 이 회사는 2020년에 분할됐다",
}

st.set_page_config(page_title="FinRAG", page_icon="📈", layout="wide")
st.title("📈 FinRAG")
st.caption("공개 문서(위키백과 · DART)를 근거로 답하고 출처를 함께 보여 주는 상장기업 리서치 서비스")
st.warning(prompts.DISCLAIMER, icon="⚠️")  # 위험 도메인 면책 안내 (F2012)


def friendly_error(e: Exception) -> str:
    """화면에는 원인 요약만. 상세는 마스킹해서 로그로. (NF2004)"""
    log.error(mask_secrets(f"{type(e).__name__}: {e}"))
    if isinstance(e, MissingKeyError):
        return f"{e.name}가 설정되지 않았습니다. 저장소 루트의 .env를 확인하세요."
    if isinstance(e, ExternalAPIError):
        return f"외부 문서 서버에 연결하지 못했습니다. 잠시 뒤 다시 시도하세요. ({e})"
    return "답변을 만드는 중 문제가 생겼습니다. 키와 네트워크를 확인한 뒤 다시 시도하세요."


# ── 사전 점검: 키 · 항목 · 색인 (F2012) ─────────────────────────────────
try:
    env("GMS_KEY")
except MissingKeyError as e:
    st.error(friendly_error(e))
    st.stop()

items = load_items()
if not items:
    st.error("항목 데이터가 없습니다. 터미널에서 `python scripts/build_items.py`를 먼저 실행하세요.")
    st.stop()
meta_count = count("meta")
if meta_count == 0:
    st.error("색인이 비어 있습니다. 터미널에서 `python scripts/index_items.py`를 실행하세요.")
    st.stop()

codes = [it["stock_code"] for it in items]


def label(code: str) -> str:
    it = item_by_code(code)
    alias = f" · {', '.join(it['aliases'])}" if it.get("aliases") else ""
    return display_name(it) + alias


def choose(code: str) -> None:
    """조건 탐색 카드에서 기업을 고를 때. selectbox의 key 값을 콜백에서 바꾼다(위젯 정체성 함정 방지)."""
    st.session_state.stock_code = code


# ── 사이드바: 기업 선택 · 조건 탐색 · 검색 설정 ───────────────────────────
with st.sidebar:
    st.header(f"{ITEM_LABEL} 선택")
    code = st.selectbox("질문할 기업", codes, index=None, key="stock_code",
                        format_func=label, placeholder="기업명이나 종목코드를 입력하세요")

    with st.expander("🔎 조건으로 기업 찾기"):
        condition = st.text_input("조건", placeholder="예) 반도체 관련 대형주, 2000년 이후 설립된 금융사")
        if st.button("찾기", width="stretch") and condition:
            with st.spinner("후보를 고르는 중..."):
                try:
                    st.session_state.explore = {"condition": condition, "results": explore(condition)}
                except Exception as e:
                    st.error(friendly_error(e))
        result = st.session_state.get("explore")
        if result:
            if not result["results"]:
                st.info("조건에 맞는 기업을 찾지 못했습니다.")
            for r in result["results"]:
                with st.container(border=True):
                    st.markdown(f"**{r['name']}** ({r['stock_code']})")
                    st.caption(r["reason"])
                    st.button("이 기업에 질문하기", key=f"pick_{r['stock_code']}",
                              on_click=choose, args=(r["stock_code"],), width="stretch")

    with st.expander("⚙️ 검색 설정 (비교용)"):
        conf = settings()["retrieval"]
        search_type = st.radio("검색 방식", ["similarity", "mmr"],
                               index=["similarity", "mmr"].index(conf["search_type"]), horizontal=True)
        top_k = st.slider("근거 수 (Top-k)", 2, 12, conf["top_k"])

    st.divider()
    st.caption(f"기업 메타데이터 색인: {meta_count}건 · 외부 문서 청크: {count('docs')}건")
    if code:
        it = item_by_code(code)
        st.caption(f"대표자: {it.get('ceo') or '-'} · 설립: {it.get('est_dt') or '-'} · 결산월: {it.get('acc_mt') or '-'}월")
        titles = cache.get(code)["titles"]
        st.caption(f"수집된 문서 {len(titles)}건" + (f": {' · '.join(titles)}" if titles else ""))
    if st.button("이 기업 대화 지우기", width="stretch", disabled=not code):
        st.session_state.chats[code] = []

if not code:
    st.info("왼쪽에서 질문할 기업을 고르거나, 조건으로 기업을 찾아보세요.")
    st.stop()

item = item_by_code(code)
st.subheader(display_name(item))

# 기업별 대화 기록을 따로 둔다 (F2009) — 기업을 바꿔도 대화가 섞이지 않는다
st.session_state.setdefault("chats", {})
messages = st.session_state.chats.setdefault(code, [])


def render_sources(cited: list[dict]) -> None:
    """답변이 실제로 인용한 근거만 보여 준다. 인용이 없으면 안내한다. (F2008)"""
    if not cited:
        st.caption("ⓘ 답변에 인용된 근거가 없습니다. 문서로 확인되지 않은 내용일 수 있습니다.")
        return
    with st.expander(f"답변에 사용한 근거 {len(cited)}건"):
        for c in cited:
            src = SOURCE_LABELS.get(c["source_type"], c["source_type"])
            link = f" · [원문]({c['url']})" if c.get("url") else ""
            st.markdown(f"**[근거 {c['n']}]** {c['title']} · {src}{link}")


for m in messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])
        if m["role"] == "assistant":
            render_sources(m.get("cited", []))

mode_label = st.radio("기능", list(MODES), horizontal=True, label_visibility="collapsed")
task = MODES[mode_label]
question = st.chat_input(INPUT_HINT[task])
if task == "briefing" and st.button("📋 브리핑 카드 만들기"):
    question = question or ""
if task == "briefing" and question is not None:
    question = prompts.BRIEFING_QUESTION + (f" 특히 '{question}'을(를) 중심으로." if question.strip() else "")

if question:
    shown = f"[{mode_label}] {question}" if task != "qa" else question
    messages.append({"role": "user", "content": shown})
    with st.chat_message("user"):
        st.markdown(shown)

    with st.chat_message("assistant"):
        answer, cited = "", []
        try:
            # 진행 상태 표시 (F2012) — 첫 질문은 수집·색인 때문에 오래 걸린다
            with st.status("관련 문서를 찾고 있습니다...", expanded=False) as status:
                report = collect(item, question, progress=status.write)
                if report.skipped_queries:
                    status.write(f"이미 수집한 검색어 {len(report.skipped_queries)}개는 건너뜀: "
                                 + ", ".join(report.skipped_queries))
                new = f"새 문서 {len(report.selected)}건 색인" if report.selected else "새로 수집한 문서 없음 (기존 색인 사용)"
                status.update(label=new, state="complete")
                if report.selected:
                    st.caption(" · ".join(report.selected))
            with st.spinner("근거를 찾아 답변을 만드는 중..."):
                result = ask(code, item["name"], question, task=task, search_type=search_type, top_k=top_k)
            answer, cited = result["answer"], result["cited"]
            if len(result["contexts"]) <= 1:
                st.info("이 기업에 대해 검색된 외부 문서가 없습니다. 기업 기본 정보만으로 답했습니다.")
        except Exception as e:
            st.error(friendly_error(e))

        if answer:
            st.markdown(answer)
            render_sources(cited)
    if answer:
        messages.append({"role": "assistant", "content": answer, "cited": cited})
