"""
存量 arXiv 论文二次过滤：扫描 papers 表里所有 is_arxiv=True 的论文，
对 title+abstract 跑 STRONG_KEYWORDS 白名单，没命中的输出样本 + 提供 cascade 删除选项。

用法:
  # dry-run：只看不删（默认）
  python scripts/filter_arxiv_papers.py

  # 真删：删 paper_scores → paper_sources → papers（cascade）
  python scripts/filter_arxiv_papers.py --apply
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
from sqlalchemy import select, delete, func

from src.db.schema import get_session, Paper, PaperScore, PaperSource
from src.pipeline.ingest_arxiv import passes_strong_filter

load_dotenv()
DB_PATH = os.getenv("DB_PATH", "./data/papers.db")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="真删（默认 dry-run）")
    parser.add_argument("--sample", type=int, default=20, help="打印多少条样本预览")
    args = parser.parse_args()

    session = get_session(DB_PATH)
    try:
        arxiv_papers = session.execute(
            select(Paper).where(Paper.is_arxiv == True)  # noqa: E712
        ).scalars().all()
        total = len(arxiv_papers)
        print(f"arXiv 论文总数: {total}")

        keep, drop = [], []
        for p in arxiv_papers:
            if passes_strong_filter(p.title, p.abstract):
                keep.append(p)
            else:
                drop.append(p)

        print(f"  命中强关键词 (保留): {len(keep)}")
        print(f"  未命中 (将删除):    {len(drop)}")

        print(f"\n=== 删除样本（前 {args.sample} 条）===")
        for p in drop[:args.sample]:
            cats = ",".join(p.arxiv_categories or [])
            print(f"  [#{p.id}] ({cats}) {p.title[:90]}")

        # 如果 drop 里有已经打分的，单独统计
        drop_ids = {p.id for p in drop}
        scored_in_drop = session.execute(
            select(func.count(PaperScore.paper_id)).where(PaperScore.paper_id.in_(drop_ids))
        ).scalar() or 0
        print(f"\n其中已打分: {scored_in_drop} 篇")

        if not args.apply:
            print(f"\n[dry-run] 未删除。如需真删，加 --apply")
            return

        if not drop:
            print("\n无可删除项")
            return

        print(f"\n[apply] 开始 cascade 删除 {len(drop)} 篇 …")
        # cascade: paper_scores → paper_sources → papers
        session.execute(delete(PaperScore).where(PaperScore.paper_id.in_(drop_ids)))
        session.execute(delete(PaperSource).where(PaperSource.paper_id.in_(drop_ids)))
        session.execute(delete(Paper).where(Paper.id.in_(drop_ids)))
        session.commit()
        print(f"  ✓ 已删除")
    finally:
        session.close()


if __name__ == "__main__":
    main()
