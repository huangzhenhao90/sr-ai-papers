import unittest
from unittest.mock import patch

from src.connectors.arxiv import (
    UR_KEYWORDS,
    SYNTHETIC_SOCIAL_KEYWORDS,
    _build_query,
    _openalex_work_to_arxiv_record,
    fetch_category,
    matches_ur_keywords,
)
from src.pipeline.ingest_arxiv import STRONG_KEYWORDS, passes_strong_filter


SYNTHETIC_SOCIAL_TERMS = set(SYNTHETIC_SOCIAL_KEYWORDS)


class ArxivRecallTests(unittest.TestCase):
    def test_ai_companion_is_a_regression_fixture_for_strong_filter(self):
        title = "Falling for Replika: Parasocial Relationships with AI Companions"
        abstract = (
            "We examine how users form parasocial relationships with AI companions "
            "and the role of intimacy and emotional attachment in long-term use."
        )

        self.assertTrue(passes_strong_filter(title, abstract))

    def test_synthetic_social_terms_reach_both_recall_stages(self):
        self.assertTrue(SYNTHETIC_SOCIAL_TERMS.issubset(set(UR_KEYWORDS)))
        self.assertTrue(SYNTHETIC_SOCIAL_TERMS.issubset(set(STRONG_KEYWORDS)))

    def test_hyphenated_human_ai_relationship_passes_strong_filter(self):
        self.assertTrue(
            passes_strong_filter(
                "Toward closer human-AI relationships",
                "Users reported growing trust and companionship with the assistant.",
            )
        )

    def test_arxiv_query_searches_title_and_abstract(self):
        query = _build_query("cs.HC", ["AI companion"])

        self.assertIn('abs:"AI companion"', query)
        self.assertIn('ti:"AI companion"', query)

    def test_openalex_fallback_maps_companion_paper_to_arxiv_record(self):
        work = {
            "id": "https://openalex.org/W7196962344",
            "doi": "https://doi.org/10.48550/arxiv.2608.04205",
            "title": "Falling for Replika: Parasocial Relationships with AI Companions",
            "publication_date": "2026-08-04",
            "publication_year": 2026,
            "abstract_inverted_index": {
                "parasocial": [0],
                "relationships": [1],
                "with": [2],
                "AI": [3],
                "companions": [4],
            },
            "authorships": [
                {"author": {"display_name": "Xiaomin Li"}},
            ],
        }

        record = _openalex_work_to_arxiv_record(work)

        self.assertEqual(record["arxiv_id"], "2608.04205")
        self.assertEqual(record["doi"], "10.48550/arxiv.2608.04205")
        self.assertEqual(record["authors"], [{"name": "Xiaomin Li"}])
        self.assertTrue(matches_ur_keywords(record["title"], record["abstract"]))

    def test_openalex_fallback_rejects_non_arxiv_work(self):
        self.assertIsNone(
            _openalex_work_to_arxiv_record(
                {
                    "doi": "https://doi.org/10.1000/example",
                    "title": "Persona study",
                }
            )
        )

    def test_openalex_local_filter_does_not_treat_social_network_as_relationship(self):
        self.assertFalse(
            matches_ur_keywords(
                "Graph neural networks for social network embedding",
                "A purely technical benchmark without human interaction studies.",
            )
        )

    @patch("src.connectors.arxiv.time.sleep", return_value=None)
    @patch("src.connectors.arxiv.get_with_retry", side_effect=TimeoutError("boom"))
    @patch("src.connectors.arxiv.make_client")
    def test_arxiv_api_failure_is_not_silently_treated_as_success(
        self, make_client, _get_with_retry, _sleep
    ):
        make_client.return_value.__enter__.return_value = object()

        with self.assertRaisesRegex(RuntimeError, "arXiv API failed.*cs.AI"):
            list(fetch_category("cs.AI", from_date="2026-07-11", max_results=1))


if __name__ == "__main__":
    unittest.main()
