"""청킹. 청크 크기와 중복 길이는 config.yaml(chunk.*)에서 바꾼다. (F2005)"""

from langchain_text_splitters import RecursiveCharacterTextSplitter

from finrag.config import settings


def split(text: str, source_type: str) -> list[str]:
    conf = settings()["chunk"][source_type]
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=conf["size"],
        chunk_overlap=conf["overlap"],
        separators=["\n\n\n", "\n\n", "\n", ". ", " ", ""],  # 위키 섹션 경계 → 문단 → 문장 순
    )
    return [c.strip() for c in splitter.split_text(text) if c.strip()]
