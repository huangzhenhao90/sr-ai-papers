import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select

from scripts import incremental_update
from src.db.schema import RawRecord, SourceRun, get_session, init_db
from src.pipeline.raw_ingest import insert_raw_record


class IncrementalIngestTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "papers.db")
        init_db(self.db_path)
        session = get_session(self.db_path)
        old_run = SourceRun(source="arxiv", params={"category": "cs.AI"})
        session.add(old_run)
        session.flush()
        insert_raw_record(
            session,
            run_id=old_run.id,
            source="arxiv",
            source_record_id="duplicate",
            payload={"arxiv_id": "duplicate"},
        )
        session.commit()
        session.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_arxiv_increment_keeps_new_records_around_a_duplicate(self):
        records = [
            {"arxiv_id": "new-before-duplicate"},
            {"arxiv_id": "duplicate"},
            {"arxiv_id": "new-after-duplicate"},
        ]

        with (
            patch.object(incremental_update, "DB_PATH", self.db_path),
            patch.object(incremental_update, "ARXIV_CATEGORIES", ["cs.AI"]),
            patch.object(incremental_update, "LOOKBACK_DAYS", 30),
            patch.object(incremental_update, "fetch_category", return_value=records),
        ):
            incremental_update.step_fetch_arxiv_incremental()

        session = get_session(self.db_path)
        try:
            latest_run = session.execute(
                select(SourceRun).order_by(SourceRun.id.desc())
            ).scalars().first()
            persisted_ids = session.execute(
                select(RawRecord.source_record_id)
                .where(RawRecord.run_id == latest_run.id)
                .order_by(RawRecord.source_record_id)
            ).scalars().all()

            self.assertEqual(latest_run.status, "success")
            self.assertEqual(latest_run.records_fetched, 2)
            self.assertEqual(
                persisted_ids,
                ["new-after-duplicate", "new-before-duplicate"],
            )
        finally:
            session.close()

    def test_arxiv_increment_uses_openalex_fallback_after_api_failure(self):
        fallback_records = [
            {
                "arxiv_id": "2608.04205",
                "title": "MatrAIx",
                "abstract": "simulated user evaluation",
            }
        ]

        with (
            patch.object(incremental_update, "DB_PATH", self.db_path),
            patch.object(incremental_update, "ARXIV_CATEGORIES", ["cs.AI"]),
            patch.object(incremental_update, "LOOKBACK_DAYS", 30),
            patch.object(
                incremental_update,
                "fetch_category",
                side_effect=RuntimeError("arXiv API unavailable"),
            ),
            patch.object(
                incremental_update,
                "fetch_arxiv_via_openalex",
                return_value=fallback_records,
            ) as fallback,
        ):
            incremental_update.step_fetch_arxiv_incremental()

        session = get_session(self.db_path)
        try:
            fallback_run = session.execute(
                select(SourceRun)
                .where(SourceRun.params.contains("openalex_fallback"))
                .order_by(SourceRun.id.desc())
            ).scalars().first()
            persisted_ids = session.execute(
                select(RawRecord.source_record_id).where(
                    RawRecord.run_id == fallback_run.id
                )
            ).scalars().all()

            fallback.assert_called_once()
            self.assertEqual(fallback_run.status, "success")
            self.assertEqual(fallback_run.records_fetched, 1)
            self.assertEqual(persisted_ids, ["2608.04205"])
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
