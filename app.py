"""상장기업 리서치 서비스 — 근거로 답하는 RAG 화면 (Streamlit). 교육생 배포 step7_skeleton.py 골격에서 출발했다.

    streamlit run app.py
"""

import logging

import streamlit as st

from finrag.collect.documents import collect
from finrag.collect.http import ExternalAPIError
from finrag.collect import cache
from finrag.config import ROOT, MissingKeyError, env, mask_secrets, settings
from finrag.domain import prompts
from finrag.domain.companies import ITEM_LABEL, display_name, item_by_code, load_items, mentioned_companies
from finrag.features.explore import explore
from finrag.rag.citations import SOURCE_LABELS
from finrag.rag import topic
from finrag.rag.graph import ask
from finrag.store.vectorstore import count
from finrag.ui_text import safe_md

(ROOT / "logs").mkdir(exist_ok=True)
logging.basicConfig(filename=ROOT / "logs" / "app.log", level=logging.INFO, encoding="utf-8",
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("finrag")

MODES = {"질문하기": "qa", "브리핑 카드": "briefing", "팩트체크": "factcheck", "용어 풀이": "term"}
INPUT_HINT = {
    "qa": "이 기업에 대해 궁금한 점을 물어보세요",
    "briefing": "브리핑에서 특히 보고 싶은 내용이 있으면 적어 주세요 (비우고 아래 버튼을 눌러도 됩니다)",
    "factcheck": "검증할 주장을 붙여 넣으세요. 예) 이 회사는 2020년에 분할됐다",
    "term": "궁금한 경제·금융 용어를 입력하세요. 예) 물적분할, PER, 공매도",
}

SERVICE_NAME = "상장기업 리서치 서비스"

st.set_page_config(page_title=SERVICE_NAME, page_icon="📈", layout="wide")
st.title(SERVICE_NAME)
st.caption("공개 문서(위키백과 · DART 공시 · 한국은행 용어집)를 근거로 답하고, 답변마다 출처를 함께 보여 줍니다.")
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


def run_later(question: str, task: str, mode_label: str, switch_to: str | None = None) -> None:
    """'다른 기업 질문' 안내의 버튼 콜백. 기업을 바꿀 수도 있고, 다음 실행에서 같은 질문을 바로 처리한다."""
    if switch_to:
        st.session_state.stock_code = switch_to
    st.session_state.run_now = {"question": question, "task": task, "mode_label": mode_label}


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
    # 색인 현황은 자리만 잡아 두고 스크립트 끝에서 채운다 — 이번 질문으로 수집한 문서까지 반영되게
    stats_box = st.empty()
    if st.button("이 기업 대화 지우기", width="stretch", disabled=not code):
        st.session_state.chats[code] = []


def fill_sidebar_stats() -> None:
    with stats_box.container():
        st.caption(f"기업 메타데이터 색인: {meta_count}건 · 외부 문서 청크: {count('docs')}건 · 용어집: {count('glossary')}개")
        if code:
            it = item_by_code(code)
            st.caption(f"대표자: {it.get('ceo') or '-'} · 설립: {it.get('est_dt') or '-'} · 결산월: {it.get('acc_mt') or '-'}월")
            titles = cache.get(code)["titles"]
            st.caption(f"수집된 문서 {len(titles)}건" + (f": {' · '.join(titles)}" if titles else ""))


if not code:
    fill_sidebar_stats()
    st.info("왼쪽에서 질문할 기업을 고르거나, 조건으로 기업을 찾아보세요.")
    st.stop()

item = item_by_code(code)
st.subheader(display_name(item))

# 기능 선택은 대화 위에 고정한다 — 대화 아래에 두면 대화가 쌓일 때마다 위치가 바뀌고 대화 중간에 끼어 보였다
mode_label = st.radio("기능", list(MODES), horizontal=True, label_visibility="collapsed")
task = MODES[mode_label]
briefing_clicked = task == "briefing" and st.button("📋 브리핑 카드 만들기")

# 기업별 대화 기록을 따로 둔다 (F2009) — 기업을 바꿔도 대화가 섞이지 않는다
st.session_state.setdefault("chats", {})
messages = st.session_state.chats.setdefault(code, [])
fill_sidebar_stats()  # 답변을 만드는 동안에도 비어 보이지 않게 먼저 그리고, 끝에서 한 번 더 갱신한다


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
        st.markdown(safe_md(m["content"]))
        if m["role"] == "assistant":
            render_sources(m.get("cited", []))

question = st.chat_input(INPUT_HINT[task])
forced = False
if "run_now" in st.session_state:  # '다른 기업 질문' 안내에서 버튼을 누른 경우
    pending = st.session_state.pop("run_now")
    question, task, mode_label, forced = pending["question"], pending["task"], pending["mode_label"], True
elif task == "briefing" and (briefing_clicked or question is not None):
    question = prompts.BRIEFING_QUESTION + (f" 특히 '{question}'을(를) 중심으로." if question and question.strip() else "")

if question:
    shown = f"[{mode_label}] {question}" if task != "qa" else question
    with st.chat_message("user"):
        st.markdown(shown)

    # 선택한 기업이 아니라 다른 기업만 묻는 질문이면 바로 답하지 않고 물어본다.
    # 그대로 답하면 그 기업 문서가 지금 기업 앞으로 수집돼 색인이 섞였다 (KT&G를 골라 놓고 삼성전자를 물은 경우)
    others = [c for c in mentioned_companies(question) if c != code]
    if not forced and task in ("qa", "factcheck") and others and code not in mentioned_companies(question):
        other = item_by_code(others[0])
        with st.chat_message("assistant"):
            st.info(f"{other['name']}에 대한 질문 같아요. 지금은 {item['name']}이(가) 선택되어 있습니다.")
            left, right = st.columns(2)
            left.button(f"{other['name']}(으)로 바꿔서 질문하기", type="primary", width="stretch",
                        on_click=run_later, args=(question, task, mode_label, other["stock_code"]))
            right.button(f"{item['name']} 기준으로 그대로 답하기", width="stretch",
                         on_click=run_later, args=(question, task, mode_label))
        fill_sidebar_stats()
        st.stop()

    # 잡담·서비스 밖 질문은 수집도 답변도 하지 않는다. 그대로 돌리면 근거에서 아무 사실이나 골라 답했다
    if not forced and task != "briefing":
        with st.spinner("질문을 확인하는 중..."):
            verdict = topic.check(item["name"], question)
        if not verdict.on_topic:
            log.info("off-topic: %s (%s)", question, verdict.reason)
            with st.chat_message("assistant"):
                st.info(prompts.OFF_TOPIC_NOTICE.format(company=item["name"]))
            fill_sidebar_stats()
            st.stop()

    with st.chat_message("assistant"):
        answer, cited = "", []
        try:
            # 진행 상태 표시 (F2012) — 첫 질문은 수집·색인 때문에 오래 걸린다
            with st.status("관련 문서를 찾고 있습니다...", expanded=False) as status:
                try:
                    report = collect(item, question, progress=status.write)
                except ExternalAPIError as e:
                    # 외부 문서 서버가 안 될 때도 이미 색인된 문서와 기업 기본 정보로는 답할 수 있다
                    log.warning(mask_secrets(f"collect failed: {e}"))
                    report = None
                    status.update(label="외부 문서를 새로 가져오지 못함 (기존 색인 사용)", state="error")
                else:
                    if report.skipped_queries:
                        status.write(f"이미 수집한 검색어 {len(report.skipped_queries)}개는 건너뜀: "
                                     + ", ".join(report.skipped_queries))
                    new = f"새 문서 {len(report.selected)}건 색인" if report.selected else "새로 수집한 문서 없음 (기존 색인 사용)"
                    status.update(label=new, state="complete")
                    if report.selected:
                        st.caption(" · ".join(report.selected))
            if report is None:
                st.warning("외부 문서 서버에 연결하지 못해, 이미 색인된 문서와 기업 기본 정보로만 답합니다.")
            for note in (report.notes if report else []):
                st.info(note)
            with st.spinner("근거를 찾아 답변을 만드는 중..."):
                result = ask(code, item["name"], question, task=task, search_type=search_type, top_k=top_k)
            answer, cited = result["answer"], result["cited"]
            if len(result["contexts"]) <= 1:
                st.info("이 기업에 대해 검색된 외부 문서가 없습니다. 기업 기본 정보만으로 답했습니다.")
        except Exception as e:
            st.error(friendly_error(e))

        if answer:
            st.markdown(safe_md(answer))
            render_sources(cited)
    # 질문과 답을 답변이 끝났을 때 함께 저장한다. 도중에 새 질문을 보내 실행이 멈추면 둘 다 남지 않는다
    # (전에는 질문을 먼저 저장해서, 멈춘 질문이 답 없이 대화에 남았다)
    if answer:
        messages.append({"role": "user", "content": shown})
        messages.append({"role": "assistant", "content": answer, "cited": cited})

fill_sidebar_stats()
