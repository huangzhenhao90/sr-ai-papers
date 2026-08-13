"""
抓取一本英文期刊的全量论文（双源：OpenAlex + Crossref），写入 raw_records。
不做去重、不做过滤，只入库。
"""

import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv

from src.db.schema import get_session, SourceRun
from src.utils.journals import by_abbr
from src.connectors import openalex as oa
from src.connectors import crossref as cr
from src.pipeline.raw_ingest import (
    assert_run_record_count,
    count_run_records,
    insert_raw_record,
)

load_dotenv()
DB_PATH = os.getenv("DB_PATH", "./data/papers.db")


def ingest_openalex(session, journal: dict, from_date: str, to_date: str | None = None) -> int:
    sid = journal.get("openalex_source_id")
    if not sid:
        print(f"  [openalex] {journal['abbr']} 无 source_id，跳过")
        return 0

    run = SourceRun(
        source="openalex",
        journal_abbr=journal["abbr"],
        params={"source_id": sid, "from_date": from_date, "to_date": to_date},
    )
    session.add(run)
    session.flush()

    seen = 0
    inserted = 0
    duplicates = 0
    try:
        for w in oa.fetch_works_by_source(sid, from_date=from_date, to_date=to_date):
            slim = oa.slim_record(w)
            seen += 1
            was_inserted = insert_raw_record(
                session,
                run_id=run.id,
                source="openalex",
                source_record_id=slim["id"],
                payload=slim,
            )
            if was_inserted:
                inserted += 1
            else:
                duplicates += 1
            if was_inserted and inserted % 100 == 0:
                session.commit()
                print(f"    已新增 {inserted} 条")
        session.commit()
        assert_run_record_count(session, run.id, expected=inserted)
        run.status = "success"
    except Exception as e:
        session.rollback()
        inserted = count_run_records(session, run.id)
        run.status = "failed"
        run.error_message = str(e)
        print(f"  [openalex] 失败: {e}")
    finally:
        run.finished_at = datetime.utcnow()
        run.records_fetched = inserted
        session.merge(run)
        session.commit()

    print(f"  [openalex] API {seen} / 新增 {inserted} / 重复 {duplicates}")
    return inserted


def ingest_crossref(session, journal: dict, from_date: str, until_date: str | None = None) -> int:
    issn = journal["issn"]
    run = SourceRun(
        source="crossref",
        journal_abbr=journal["abbr"],
        params={"issn": issn, "from_pub_date": from_date, "until_pub_date": until_date},
    )
    session.add(run)
    session.flush()

    seen = 0
    inserted = 0
    duplicates = 0
    try:
        for w in cr.fetch_works_by_issn(issn, from_pub_date=from_date, until_pub_date=until_date):
            slim = cr.slim_record(w)
            doi = slim.get("doi")
            if not doi:
                continue
            seen += 1
            was_inserted = insert_raw_record(
                session,
                run_id=run.id,
                source="crossref",
                source_record_id=doi,
                payload=slim,
            )
            if was_inserted:
                inserted += 1
            else:
                duplicates += 1
            if was_inserted and inserted % 100 == 0:
                session.commit()
                print(f"    已新增 {inserted} 条")
        session.commit()
        assert_run_record_count(session, run.id, expected=inserted)
        run.status = "success"
    except Exception as e:
        session.rollback()
        inserted = count_run_records(session, run.id)
        run.status = "failed"
        run.error_message = str(e)
        print(f"  [crossref] 失败: {e}")
    finally:
        run.finished_at = datetime.utcnow()
        run.records_fetched = inserted
        session.merge(run)
        session.commit()

    print(f"  [crossref] API {seen} / 新增 {inserted} / 重复 {duplicates}")
    return inserted


def main(abbr: str, from_date: str = "2023-01-01", to_date: str | None = None):
    journal = by_abbr(abbr)
    if not journal:
        print(f"未找到期刊: {abbr}")
        return

    print(f"\n=== 抓取 {journal['abbr']} ({journal['name_en']}) ===")
    print(f"日期: {from_date} → {to_date or '至今'}\n")

    session = get_session(DB_PATH)
    try:
        print("[1/2] OpenAlex …")
        n_oa = ingest_openalex(session, journal, from_date, to_date)
        print(f"  -> {n_oa} 条\n")

        print("[2/2] Crossref …")
        n_cr = ingest_crossref(session, journal, from_date, to_date)
        print(f"  -> {n_cr} 条\n")

        print(f"=== 完成: OpenAlex {n_oa} + Crossref {n_cr} 条原始记录 ===")
    finally:
        session.close()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("abbr", help="期刊缩写，如 JOB")
    p.add_argument("--from-date", default="2023-01-01")
    p.add_argument("--to-date", default=None)
    args = p.parse_args()
    main(args.abbr, args.from_date, args.to_date)
