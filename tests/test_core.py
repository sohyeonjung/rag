"""키 없이 돌아가는 단위 테스트.

    python -m unittest discover -s tests
"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from finrag.collect.krx import _is_common_stock  # noqa: E402
from finrag.config import mask_secrets  # noqa: E402
from finrag.domain.companies import build_item_text, item_metadata  # noqa: E402
from finrag.rag.citations import cited_numbers, cited_sources  # noqa: E402
from finrag.store.chunking import split  # noqa: E402

ITEM = {
    "stock_code": "005930", "name": "삼성전자", "name_eng": "Samsung Electronics Co,.Ltd",
    "aliases": ["삼전"], "ceo": "홍길동", "est_dt": "19690113", "acc_mt": "12",
    "rank": 1, "rank_as_of": "2026-10-21", "summary": "반도체와 전자제품을 만드는 기업이다.",
}


class CitationTest(unittest.TestCase):
    def test_parses_single_and_grouped(self):
        self.assertEqual(cited_numbers("A [근거 1] B [근거 2, 4] C [근거3]"), {1, 2, 3, 4})

    def test_parses_repeated_label_inside_brackets(self):
        # 실제 답변에서 나온 형식: 화면에 2건만 표시되던 버그 (트러블슈팅 #8)
        self.assertEqual(cited_numbers("[근거 7, 근거 8] 그리고 [근거 1, 근거 4, 근거 6]"), {1, 4, 6, 7, 8})

    def test_range_reference_is_not_a_citation(self):
        answer = "다만 제공된 문서([근거 1]~[근거 8])에서는 확인되지 않습니다. 설립일은 2016년이다 [근거 3]."
        self.assertEqual(cited_numbers(answer), {3})
        self.assertEqual(cited_numbers("[근거 1~8]에서 찾을 수 없음"), set())

    def test_ignores_non_citation_brackets(self):
        self.assertEqual(cited_numbers("[참고 3] [근거 없음] 2026년"), set())

    def test_only_cited_sources_are_kept(self):
        contexts = [{"n": i, "title": f"t{i}"} for i in range(1, 5)]
        self.assertEqual([c["n"] for c in cited_sources("x [근거 3]", contexts)], [3])

    def test_no_citation_returns_empty(self):
        self.assertEqual(cited_sources("근거 없는 답", [{"n": 1}]), [])


class ItemTest(unittest.TestCase):
    def test_item_text_has_search_attributes(self):
        text = build_item_text(ITEM)
        for part in ("삼성전자", "005930", "삼전", "1969-01-13", "12월", "반도체"):
            self.assertIn(part, text)

    def test_metadata_is_scalar_and_code_keeps_leading_zero(self):
        meta = item_metadata(ITEM)
        self.assertEqual(meta["stock_code"], "005930")
        self.assertEqual(meta["est_year"], 1969)
        self.assertTrue(all(isinstance(v, (str, int, float, bool)) for v in meta.values()))


class DartParseTest(unittest.TestCase):
    def test_table_row_becomes_one_line(self):
        from finrag.collect.dart_report import _clean
        xml = ("<P>가. 주요 제품</P><TABLE><TR><TH>부 문</TH><TH>매출액</TH></TR>"
               "<TR><TD><P>DS</P></TD><TE>2,092,317</TE></TR></TABLE>")
        self.assertEqual(_clean(xml), "가. 주요 제품\n부 문 | 매출액\nDS | 2,092,317")


class GlossaryTest(unittest.TestCase):
    def test_term_names_split_parentheses_and_slashes(self):
        from finrag.rag.glossary_lookup import _names
        self.assertEqual(_names("기업분할(물적분할, 인적분할)"), ["기업분할", "물적분할", "인적분할"])
        self.assertEqual(_names("주가수익비율(PER)"), ["주가수익비율", "PER"])
        self.assertEqual(_names("간접금융/직접금융"), ["간접금융", "직접금융"])

    def test_english_abbreviation_needs_word_boundary(self):
        from finrag.rag.glossary_lookup import _mentioned
        self.assertTrue(_mentioned("PER이 뭐야", "PER"))
        self.assertFalse(_mentioned("PERCENT 단위", "PER"))

    def test_pdf_line_join_keeps_word_boundaries(self):
        from finrag.collect.glossary import _join
        self.assertEqual(_join(["가계가 마음대로 소비와 저", "축으로 처분할 수 ", "있는 소득"]),
                         "가계가 마음대로 소비와 저축으로 처분할 수 있는 소득")


class MiscTest(unittest.TestCase):
    def test_mask_secrets_hides_query_keys(self):
        os.environ["DART_API_KEY"] = "abc123secret"
        masked = mask_secrets("https://x/api?crtfc_key=abc123secret&corp_code=1 abc123secret")
        self.assertNotIn("abc123secret", masked)

    def test_common_stock_filter(self):
        self.assertTrue(_is_common_stock("005930", "삼성전자"))
        self.assertFalse(_is_common_stock("005935", "삼성전자우"))

    def test_chunks_respect_size(self):
        chunks = split("가나다라마. " * 500, "wiki")
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c) <= 1000 for c in chunks))


if __name__ == "__main__":
    unittest.main()
