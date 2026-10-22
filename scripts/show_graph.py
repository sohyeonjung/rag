"""LangGraph 구조 출력 (검증 4.3 — retrieve/generate 두 노드 확인).

    python scripts/show_graph.py
"""

import _bootstrap  # noqa: F401

from finrag.rag.graph import build_graph

print(build_graph().get_graph().draw_mermaid())
