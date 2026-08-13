"""
抓取 arXiv 5 个分类下与 AI 社交关系相关的论文，写入 raw_records，再 normalize 入 papers。

复用 P1 的 normalizer 逻辑会复杂（字段不一），所以这里直接 normalize：
arxiv_id 作为去重键，DOI 也存（少数论文有 DOI）。

【二次过滤】arXiv fetch 阶段用 UR_KEYWORDS 召回（含一些弱信号词如 social interaction / dating），
所以 normalize 时再用 STRONG_KEYWORDS 严格白名单做一道过滤，没命中强信号的 raw_record 不入 papers。
"""

import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv
from sqlalchemy import select

from src.db.schema import get_session, SourceRun, RawRecord, Paper, PaperSource
from src.connectors.arxiv import SYNTHETIC_SOCIAL_KEYWORDS, fetch_category
from src.pipeline.raw_ingest import (
    assert_run_record_count,
    count_run_records,
    insert_raw_record,
)

load_dotenv()
DB_PATH = os.getenv("DB_PATH", "./data/papers.db")

CATEGORIES = ["cs.HC", "cs.CL", "cs.CY", "cs.SI", "cs.AI"]
FROM_DATE = "2023-01-01"

# 强信号关键词白名单：必须命中至少一个，arXiv raw_record 才会被提升为 paper。
# 这套词是 social_relationship.en 的精选子集，去掉了 social interaction / dating / empathy 等
# 容易在非关系类论文里出现的弱信号。
# 设计原则：出现这些词几乎一定意味着论文有人际/人机关系、情感连接或社交互动实质内容。
STRONG_KEYWORDS = [
    # 拟社会关系（最强信号）
    "parasocial relationship", "parasocial interaction", "parasocial",
    # 亲密 / 浪漫关系
    "romantic relationship", "romantic partner", "intimate relationship",
    "intimacy", "falling in love", "dating", "AI girlfriend", "AI boyfriend",
    # 陪伴 / 关系
    "companionship", "companion", "virtual companion", "AI companion",
    "chatbot companion", "social companion", "digital companion",
    "companion robot", "companion chatbot",
    "relationship formation", "relationship maintenance",
    "interpersonal relationship", "social relationship",
    "human-AI relationship", "human-robot relationship",
    "human-AI friendship", "human-AI romantic", "human-AI attachment",
    "AI partner", "AI friend", "virtual partner", "virtual friend",
    # 情感 / 依恋
    "emotional attachment", "attachment theory",
    "emotional bond", "emotional connection",
    "emotional support", "loneliness", "social isolation",
    "grief", "bereavement", "griefbot",
    # 拟人化 / 社会临场
    "anthropomorphism", "anthropomorphic", "personification",
    "humanlikeness", "humanness",
    "social presence", "co-presence",
    "social robot", "social actor",
    # 关系型 AI
    "relational agent", "relational chatbot", "relational AI",
    "chatbot relationship", "chatbot relationships",
    "AI therapist", "chatbot therapist",
    "embodied conversational agent",
    "trust in AI", "trust in chatbot", "trust in robot",
    "interpersonal trust", "trust formation",
    "AI-mediated communication",
    *SYNTHETIC_SOCIAL_KEYWORDS,
]

_STRONG_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in STRONG_KEYWORDS) + r")\b",
    re.IGNORECASE,
)
_HYPHEN_RE = re.compile(r"[-‐‑‒–—]")


def passes_strong_filter(title: str | None, abstract: str | None) -> bool:
    """abstract+title 命中至少一个强信号词才返回 True。"""
    text = (title or "") + " " + (abstract or "")
    normalized_text = _HYPHEN_RE.sub(" ", text)
    return bool(_STRONG_RE.search(text) or _STRONG_RE.search(normalized_text))


def ingest():
    session = get_session(DB_PATH)
    try:
        for cat in CATEGORIES:
            print(f"\n=== arXiv {cat} ===")
            run = SourceRun(
                source="arxiv",
                journal_abbr=None,
                params={"category": cat, "from_date": FROM_DATE},
            )
            session.add(run)
            session.flush()

            seen = 0
            inserted = 0
            duplicates = 0
            try:
                for rec in fetch_category(cat, from_date=FROM_DATE):
                    seen += 1
                    was_inserted = insert_raw_record(
                        session,
                        run_id=run.id,
                        source="arxiv",
                        source_record_id=rec["arxiv_id"],
                        payload=rec,
                    )
                    if was_inserted:
                        inserted += 1
                    else:
                        duplicates += 1
                    if was_inserted and inserted % 50 == 0:
                        session.commit()
                        print(f"    已新增 {inserted} 条")
                session.commit()
                assert_run_record_count(session, run.id, expected=inserted)
                run.status = "success"
            except Exception as e:
                session.rollback()
                inserted = count_run_records(session, run.id)
                run.status = "failed"
                run.error_message = str(e)[:500]
                print(f"  ! {e}")
            finally:
                run.records_fetched = inserted
                run.finished_at = datetime.utcnow()
                session.merge(run)
                session.commit()

            print(
                f"  -> API {seen} / 新增 {inserted} / 重复 {duplicates}"
            )
    finally:
        session.close()


def normalize_arxiv():
    """把 arXiv raw_records 提升为 papers + paper_sources。

    【二次过滤】raw_record 必须命中 STRONG_KEYWORDS 至少一个，才被提升为 paper。
    没命中的直接跳过（raw_record 仍保留在 raw_records 表，便于以后回溯/重新过滤）。
    """
    session = get_session(DB_PATH)
    try:
        existing_raw_ids = set(
            session.execute(select(PaperSource.raw_record_id)).scalars().all()
        )
        rows = session.execute(
            select(RawRecord).where(RawRecord.source == "arxiv")
        ).scalars().all()
        new_rows = [r for r in rows if r.id not in existing_raw_ids]
        print(f"待 normalize 的 arXiv raw: {len(new_rows)}")

        n_new = 0
        n_merged = 0
        n_filtered = 0  # 被强关键词过滤掉的
        for r in new_rows:
            payload = r.payload
            title = payload.get("title") or ""
            abstract = payload.get("abstract") or ""

            # 二次过滤：未命中强信号词的 raw_record 不入 papers
            if not passes_strong_filter(title, abstract):
                n_filtered += 1
                continue
            arxiv_id = payload.get("arxiv_id")
            doi = (payload.get("doi") or "").lower() or None

            # 同一 arxiv_id 视为同一论文
            paper = session.execute(
                select(Paper).where(Paper.arxiv_id == arxiv_id)
            ).scalar_one_or_none()
            # 也可能 DOI 已存在（被期刊源收录）
            if not paper and doi:
                paper = session.execute(
                    select(Paper).where(Paper.doi == doi)
                ).scalar_one_or_none()

            if paper:
                # 合并: 补 arxiv_id, abstract, pdf_url
                if not paper.arxiv_id:
                    paper.arxiv_id = arxiv_id
                if not paper.arxiv_categories:
                    paper.arxiv_categories = payload.get("categories")
                if not paper.abstract and payload.get("abstract"):
                    paper.abstract = payload.get("abstract")
                if not paper.pdf_url and payload.get("pdf_url"):
                    paper.pdf_url = payload.get("pdf_url")
                paper.is_arxiv = True
                n_merged += 1
                is_primary = False
            else:
                paper = Paper(
                    doi=doi,
                    arxiv_id=arxiv_id,
                    arxiv_categories=payload.get("categories"),
                    is_arxiv=True,
                    title=payload.get("title") or "(无标题)",
                    abstract=payload.get("abstract"),
                    authors=payload.get("authors"),
                    journal_abbr=None,
                    journal_name=f"arXiv:{payload.get('primary_category', '')}",
                    pub_year=payload.get("publication_year"),
                    pub_date=payload.get("publication_date"),
                    pdf_url=payload.get("pdf_url"),
                    landing_page_url=payload.get("landing_page_url"),
                    lang="en",
                )
                session.add(paper)
                session.flush()
                n_new += 1
                is_primary = True

            session.add(PaperSource(
                paper_id=paper.id,
                raw_record_id=r.id,
                source="arxiv",
                is_primary=is_primary,
            ))

            if (n_new + n_merged) % 100 == 0:
                session.commit()
        session.commit()
        print(f"完成: 新增 {n_new} 篇 arXiv 论文 / 合并 {n_merged} 条到已有论文 / 被强关键词过滤 {n_filtered} 条")
    finally:
        session.close()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["fetch", "normalize", "all"])
    args = p.parse_args()
    if args.cmd in ("fetch", "all"):
        ingest()
    if args.cmd in ("normalize", "all"):
        normalize_arxiv()
