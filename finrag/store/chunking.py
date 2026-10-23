"""청킹. 청크 크기와 중복 길이는 config.yaml(chunk.*)에서 바꾼다. (F2005)"""

from langchain_text_splitters import RecursiveCharacterTextSplitter

from finrag.collect.dart_report import TABLE_END, TABLE_HEAD
from finrag.config import settings


def split(text: str, source_type: str) -> list[str]:
    conf = settings()["chunk"][source_type]
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=conf["size"],
        chunk_overlap=conf["overlap"],
        separators=["\n\n\n", "\n\n", "\n", ". ", " ", ""],  # 위키 섹션 경계 → 문단 → 문장 순
    )
    return [c.strip() for c in splitter.split_text(text) if c.strip()]


def split_dart(text: str) -> list[str]:
    """공시용 청킹: 줄(표 행) 단위로 채우고, 표 중간에서 시작하는 청크에는 그 표의 머리글 행을 다시 붙인다.

    일반 분할기는 표를 글자 수로만 잘라, 머리글(자산항목 | 소재지 | 기초장부가액 | …)은 앞 청크에 남고
    뒤 청크에는 '프랑스 | 건설중인자산 | 6,696 | 1,232 | 0 | 0 | 7,928'처럼 숫자만 남았다. (트러블슈팅 #14)
    """
    conf = settings()["chunk"]["dart"]
    size, overlap = conf["size"], conf["overlap"]
    long_line = RecursiveCharacterTextSplitter(chunk_size=size, chunk_overlap=overlap)

    # (줄, 그 줄이 속한 표의 머리글) — 머리글 줄 자신은 머리글을 갖지 않는다
    rows: list[tuple[str, tuple[str, ...]]] = []
    head: list[str] = []
    prev_was_head = False
    for line in text.split("\n"):
        if line == TABLE_END:
            head, prev_was_head = [], False
            continue
        is_head = line.startswith(TABLE_HEAD)
        if is_head:
            head = [*head, line] if prev_was_head else [line]  # 여러 줄 머리글은 이어 붙인다
        prev_was_head = is_head
        for piece in ([line] if len(line) <= size else long_line.split_text(line)):
            rows.append((piece, () if is_head else tuple(head)))

    def length(lines: list[str]) -> int:
        return sum(len(x) + 1 for x in lines)

    chunks: list[list[str]] = []
    cur: list[str] = []
    for line, line_head in rows:
        if cur and length(cur) + len(line) > size:
            chunks.append(cur)
            tail: list[str] = []  # 앞 청크 끝부분을 overlap 길이만큼 이어 받는다
            for x in reversed(cur):
                if length(tail) + len(x) > overlap:
                    break
                tail.insert(0, x)
            cur = [x for x in tail if not x.startswith(TABLE_HEAD)]
        if line_head and not any(x.startswith(TABLE_HEAD) for x in cur):
            cur = [*line_head, *cur]
        cur.append(line)
    if cur:
        chunks.append(cur)
    return [c for c in ("\n".join(ch).strip() for ch in chunks) if c]
