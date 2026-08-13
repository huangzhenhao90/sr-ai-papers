import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select

from src.db.schema import RawRecord, SourceRun, get_session, init_db
from src.pipeline.raw_ingest import (
    RawRecordWriteError,
    assert_run_record_count,
    insert_raw_record,
)


class RawIngestTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "papers.db")
        init_db(self.db_path)
        self.session = get_session(self.db_path)
        self.run = SourceRun(source="arxiv", params={"category": "cs.AI"})
        self.session.add(self.run)
        self.session.flush()

    def tearDown(self):
        self.session.close()
        self.temp_dir.cleanup()

    def test_duplicate_rolls_back_only_its_own_savepoint(self):
        self.assertTrue(
            insert_raw_record(
                self.session,
                run_id=self.run.id,
                source="arxiv",
                source_record_id="2608.04205",
                payload={"title": "MatrAIx"},
            )
        )
        self.assertFalse(
            insert_raw_record(
                self.session,
                run_id=self.run.id,
                source="arxiv",
                source_record_id="2608.04205",
                payload={"title": "MatrAIx duplicate"},
            )
        )
        self.assertTrue(
            insert_raw_record(
                self.session,
                run_id=self.run.id,
                source="arxiv",
                source_record_id="2608.04206",
                payload={"title": "Next paper"},
            )
        )
        self.session.commit()

        records = self.session.execute(
            select(RawRecord)
            .where(RawRecord.run_id == self.run.id)
            .order_by(RawRecord.source_record_id)
        ).scalars().all()

        self.assertEqual(
            [record.source_record_id for record in records],
            ["2608.04205", "2608.04206"],
        )
        assert_run_record_count(self.session, self.run.id, expected=2)

    def test_count_mismatch_is_not_reported_as_success(self):
        with self.assertRaisesRegex(RuntimeError, "persisted 0 raw records; expected 1"):
            assert_run_record_count(self.session, self.run.id, expected=1)

    def test_non_duplicate_database_error_is_not_swallowed(self):
        with self.assertRaisesRegex(RawRecordWriteError, "source=arxiv record_id=broken"):
            insert_raw_record(
                self.session,
                run_id=self.run.id,
                source="arxiv",
                source_record_id="broken",
                payload={"not_json_serializable": object()},
            )


if __name__ == "__main__":
    unittest.main()
