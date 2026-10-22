"""Chroma 컬렉션. 프로세스마다 같은 폴더에 클라이언트를 하나만 만든다."""

from functools import lru_cache

from langchain_chroma import Chroma

from finrag.config import path, settings
from finrag.llm import embeddings


@lru_cache
def collection(kind: str) -> Chroma:
    """kind: meta(기업 메타데이터) · docs(외부 문서 청크)"""
    return Chroma(
        collection_name=settings()["collections"][kind],
        embedding_function=embeddings(),
        persist_directory=str(path("chroma_dir")),
        collection_metadata={"hnsw:space": "cosine"},
    )


def count(kind: str) -> int:
    return collection(kind)._collection.count()
