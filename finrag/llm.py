"""GMS(OpenAI 호환) 클라이언트. 모델은 MODEL_PROFILE에 따라 역할별로 고른다."""

from functools import lru_cache

from langchain_core.globals import set_llm_cache
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from finrag.config import ROOT, env, model_name, settings

_cache_ready = False


def _enable_cache() -> None:
    """개발 중 같은 프롬프트로 LLM을 다시 부르지 않게 SQLite 캐시를 켠다. (크레딧 절약)"""
    global _cache_ready
    if _cache_ready or not settings().get("llm_cache"):
        return
    from langchain_community.cache import SQLiteCache

    set_llm_cache(SQLiteCache(database_path=str(ROOT / ".llm_cache.sqlite")))
    _cache_ready = True


@lru_cache
def chat(role: str) -> ChatOpenAI:
    """role: query(P1 검색어 추출) · select(P2 후보 선별) · answer(P3 답변 생성)"""
    _enable_cache()
    return ChatOpenAI(
        model=model_name(role),
        api_key=env("GMS_KEY"),
        base_url=settings()["gms_base_url"],
        timeout=60,
        max_retries=2,
    )


@lru_cache
def embeddings() -> OpenAIEmbeddings:
    return OpenAIEmbeddings(
        model=settings()["embedding_model"],
        api_key=env("GMS_KEY"),
        base_url=settings()["gms_base_url"],
    )
