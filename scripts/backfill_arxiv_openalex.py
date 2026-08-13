"""
一次性 arXiv 全量回填（OpenAlex 路径）。

arXiv Query API 对关键词分块请求会 429 限流，这里改用 OpenAlex 的 arXiv
源（S4306400194）+ 关键词 search 分块抓 2023-01-01 至今的论文，
写入 raw_records（source=arxiv），最后 normalize_arxiv 入 papers。

用法:
  python scripts/backfill_arxiv_openalex.py
"""

import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from src.db.schema import get_session, SourceRun
from src.connectors.arxiv import fetch_arxiv_via_openalex
from src.pipeline.raw_ingest import (
    assert_run_record_count,
    count_run_records,
    insert_raw_record,
)
from src.pipeline.ingest_arxiv import normalize_arxiv

load_dotenv()
DB_PATH = os.getenv("DB_PATH", "./data/papers.db")
FROM_DATE = "2023-01-01"


def main():
    session = get_session(DB_PATH)
    try:
        run = SourceRun(
            source="arxiv",
            journal_abbr=None,
            params={"mode": "openalex_backfill", "from_date": FROM_DATE},
        )
        session.add(run)
        session.flush()
        seen = 0
        inserted = 0
        duplicates = 0
        try:
            for record in fetch_arxiv_via_openalex(FROM_DATE):
                seen += 1
                if insert_raw_record(
                    session,
                    run_id=run.id,
                    source="arxiv",
                    source_record_id=record["arxiv_id"],
                    payload=record,
                ):
                    inserted += 1
                else:
                    duplicates += 1
                if inserted % 500 == 0:
                    session.commit()
                    print(f"  已新增 {inserted} 条")
            session.commit()
            assert_run_record_count(session, run.id, expected=inserted)
            run.status = "success"
        except Exception as error:
            session.rollback()
            inserted = count_run_records(session, run.id)
            run.status = "failed"
            run.error_message = str(error)[:500]
            print(f"  arXiv OpenAlex 回填失败: {error}")
        finally:
            run.records_fetched = inserted
            run.finished_at = datetime.utcnow()
            session.merge(run)
            session.commit()
        print(f"  arXiv OpenAlex 回填: 候选 {seen} / 新增 {inserted} / 重复 {duplicates}")
    finally:
        session.close()

    print("\n=== normalize_arxiv ===")
    normalize_arxiv()


if __name__ == "__main__":
    main()
