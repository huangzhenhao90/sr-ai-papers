"""
元数据补全：先入队，再分批跑两个补全源。

策略：
1. enqueue: 扫 papers 表，把缺摘要 / 缺 PDF / 缺引用数的论文塞进 enrichment_queue
2. run_s2: 拉队列里"需要 abstract"的，批量调 Semantic Scholar
3. run_upw: 拉队列里"需要 oa_status / pdf"的，逐个调 Unpaywall
4. 处理完更新 papers，把对应队列项标 done
"""

import os
import sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv
from sqlalchemy import select, update

from src.db.schema import get_session, Paper, EnrichmentQueue
from src.connectors import semantic_scholar as s2
from src.connectors import unpaywall as upw

load_dotenv()
DB_PATH = os.getenv("DB_PATH", "./data/papers.db")

ABS_MIN_LEN = 50  # 短于此视为"无摘要"


# ---------- 入队 ----------
def enqueue():
    session = get_session(DB_PATH)
    try:
        papers = session.execute(select(Paper)).scalars().all()
        existing = set(
            session.execute(select(EnrichmentQueue.paper_id)).scalars().all()
        )
        added = 0
        for p in papers:
            if p.id in existing:
                continue
            needs = []
            if not p.abstract or len(p.abstract or "") < ABS_MIN_LEN:
                needs.append("abstract")
            if not (p.open_access_url or p.pdf_url):
                needs.append("oa")
            if needs and p.doi:  # 没 DOI 就补不了
                session.add(EnrichmentQueue(
                    paper_id=p.id,
                    needs=needs,
                    priority=1 if "abstract" in needs else 0,
                ))
                added += 1
        session.commit()
        print(f"入队 {added} 条待补全任务")
    finally:
        session.close()


# ---------- 跑 Semantic Scholar ----------
def run_s2(limit: int = 10000, retry_failed: bool = False):
    session = get_session(DB_PATH)
    try:
        # 选出需要 abstract 的；可选包含上次失败
        statuses = ["pending"]
        if retry_failed:
            statuses.append("failed")
            # 重置 failed 为 pending（仅限 S2 失败，"S2 无 abstract 字段" 等原因）
            from sqlalchemy import update
            session.execute(
                update(EnrichmentQueue)
                .where(EnrichmentQueue.status == "failed")
                .where(EnrichmentQueue.last_error.like("%S2%"))
                .values(status="pending")
            )
            session.commit()

        rows = session.execute(
            select(EnrichmentQueue, Paper).join(Paper, EnrichmentQueue.paper_id == Paper.id)
            .where(EnrichmentQueue.status == "pending")
            .limit(limit)
        ).all()
        rows = [(q, p) for q, p in rows if "abstract" in (q.needs or []) and p.doi]
        print(f"Semantic Scholar: 待处理 {len(rows)} 条")

        if not rows:
            return

        doi_to_pq = {p.doi: (p, q) for q, p in rows}
        dois = list(doi_to_pq.keys())

        # 分块跑
        n_filled = 0
        n_no_data = 0
        for chunk_start in range(0, len(dois), 500):
            chunk = dois[chunk_start : chunk_start + 500]
            print(f"  调 S2 batch {chunk_start//500 + 1} ({len(chunk)} DOIs)…")
            data = s2.fetch_by_dois(chunk)
            for doi in chunk:
                p, q = doi_to_pq[doi]
                rec = data.get(doi)
                q.attempts += 1
                q.last_attempt_at = datetime.utcnow()
                if rec:
                    abs_text = rec.get("abstract")
                    if abs_text and len(abs_text) >= ABS_MIN_LEN:
                        p.abstract = abs_text
                        n_filled += 1
                    cc = rec.get("citationCount")
                    if cc and cc > (p.cited_by_count or 0):
                        p.cited_by_count = cc
                    pdf = (rec.get("openAccessPdf") or {}).get("url")
                    if pdf and not p.pdf_url:
                        p.pdf_url = pdf
                    q.status = "done" if abs_text else "failed"
                    q.last_error = None if abs_text else "S2 无 abstract 字段"
                else:
                    n_no_data += 1
                    q.status = "failed"
                    q.last_error = "S2 无此 DOI"
            session.commit()
        print(f"S2 完成: 补到 abstract {n_filled} / 无数据 {n_no_data}")
    finally:
        session.close()


# ---------- 跑 Unpaywall ----------
def run_upw(limit: int = 5000):
    session = get_session(DB_PATH)
    try:
        rows = session.execute(
            select(EnrichmentQueue, Paper).join(Paper, EnrichmentQueue.paper_id == Paper.id)
            .where(EnrichmentQueue.status.in_(["pending", "failed"]))
            .limit(limit)
        ).all()
        rows = [(q, p) for q, p in rows if "oa" in (q.needs or []) and p.doi and not (p.pdf_url or p.open_access_url)]
        print(f"Unpaywall: 待处理 {len(rows)} 条")
        if not rows:
            return

        n_filled = 0
        for q, p in rows:
            rec = upw.fetch_one(p.doi)
            q.attempts += 1
            q.last_attempt_at = datetime.utcnow()
            if rec and rec.get("is_oa"):
                best = rec.get("best_oa_location") or {}
                pdf = best.get("url_for_pdf") or best.get("url")
                if pdf:
                    p.pdf_url = pdf
                    p.open_access_url = pdf
                    n_filled += 1
                # 不动 status，因为可能 abstract 还没补
            session.commit()
        print(f"Unpaywall 完成: 补到 PDF {n_filled}")
    finally:
        session.close()


# ---------- 报告 ----------
def report():
    session = get_session(DB_PATH)
    try:
        from sqlalchemy import func
        total = session.execute(select(func.count(Paper.id))).scalar()
        with_abs = session.execute(
            select(func.count(Paper.id)).where(func.length(Paper.abstract) >= ABS_MIN_LEN)
        ).scalar()
        with_oa = session.execute(
            select(func.count(Paper.id)).where(
                (Paper.pdf_url.isnot(None)) | (Paper.open_access_url.isnot(None))
            )
        ).scalar()
        q_pending = session.execute(
            select(func.count(EnrichmentQueue.id)).where(EnrichmentQueue.status == "pending")
        ).scalar()
        q_done = session.execute(
            select(func.count(EnrichmentQueue.id)).where(EnrichmentQueue.status == "done")
        ).scalar()
        q_failed = session.execute(
            select(func.count(EnrichmentQueue.id)).where(EnrichmentQueue.status == "failed")
        ).scalar()
        print(f"\n=== Enrichment 当前状态 ===")
        print(f"papers 总数:          {total}")
        print(f"  有 abstract (≥{ABS_MIN_LEN}字): {with_abs} ({with_abs*100//total}%)")
        print(f"  有 PDF/OA:               {with_oa} ({with_oa*100//total}%)")
        print(f"队列: pending={q_pending}  done={q_done}  failed={q_failed}")
    finally:
        session.close()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["enqueue", "s2", "upw", "report", "all"])
    p.add_argument("--limit", type=int, default=10000)
    args = p.parse_args()
    if args.cmd in ("enqueue", "all"):
        enqueue()
    if args.cmd in ("s2", "all"):
        run_s2(args.limit, retry_failed=True)
    if args.cmd in ("upw", "all"):
        run_upw(args.limit)
    if args.cmd in ("report", "all"):
        report()
