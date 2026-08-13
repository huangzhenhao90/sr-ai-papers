"""
Crossref connector: 按 ISSN + 日期范围全量抓取期刊文章。

策略：
- 历史回溯用 from-pub-date / until-pub-date
- 增量用 from-index-date（Crossref 文档推荐周期同步用 index-date）
- 使用 cursor (next-cursor) 分页
- rows 上限 1000
"""

import os
import time
from typing import Iterator

from src.utils.http import make_client, get_with_retry

CROSSREF_WORKS = "https://api.crossref.org/works"
ROWS = 500
RATE_SLEEP = 0.2


def fetch_works_by_issn(
    issn: str,
    from_pub_date: str | None = None,
    until_pub_date: str | None = None,
    from_index_date: str | None = None,
    contact: str | None = None,
) -> Iterator[dict]:
    """生成器：逐条产出某 ISSN 在日期范围内的所有 works。"""
    contact = contact or os.getenv("CONTACT_EMAIL", "anonymous@example.com")

    filters = [f"issn:{issn}", "type:journal-article"]
    if from_pub_date:
        filters.append(f"from-pub-date:{from_pub_date}")
    if until_pub_date:
        filters.append(f"until-pub-date:{until_pub_date}")
    if from_index_date:
        filters.append(f"from-index-date:{from_index_date}")

    cursor = "*"
    with make_client() as client:
        while cursor:
            params = {
                "filter": ",".join(filters),
                "rows": ROWS,
                "cursor": cursor,
                "mailto": contact,
            }
            r = get_with_retry(client, CROSSREF_WORKS, params=params)
            data = r.json()
            msg = data.get("message", {})
            items = msg.get("items", [])
            if not items:
                break
            for item in items:
                yield item
            next_cursor = msg.get("next-cursor")
            # Crossref 在没有更多结果时仍会返回 cursor，需检查 items 长度
            if len(items) < ROWS:
                break
            cursor = next_cursor
            time.sleep(RATE_SLEEP)


def slim_record(w: dict) -> dict:
    """裁剪 Crossref 原始记录。"""
    issued = w.get("issued", {}).get("date-parts", [[None]])[0]
    pub_year = issued[0] if issued and issued[0] else None
    pub_date = "-".join(str(x).zfill(2) for x in issued if x) if issued and issued[0] else None

    title = (w.get("title") or [None])[0]
    abstract = w.get("abstract")  # Crossref 摘要含 JATS 标签，需后处理
    if abstract:
        # 去掉 <jats:p> 等标签的简单处理
        import re
        abstract = re.sub(r"<[^>]+>", "", abstract).strip()

    return {
        "doi": w.get("DOI"),
        "title": title,
        "subtitle": (w.get("subtitle") or [None])[0],
        "publication_year": pub_year,
        "publication_date": pub_date,
        "type": w.get("type"),
        "language": w.get("language"),
        "is_referenced_by_count": w.get("is-referenced-by-count"),
        "abstract": abstract,
        "authors": [
            {
                "name": " ".join(filter(None, [a.get("given"), a.get("family")])),
                "orcid": a.get("ORCID"),
                "affiliation": [aff.get("name") for aff in (a.get("affiliation") or [])],
            }
            for a in (w.get("author") or [])
        ],
        "container_title": (w.get("container-title") or [None])[0],
        "issn": w.get("ISSN") or [],
        "volume": w.get("volume"),
        "issue": w.get("issue"),
        "page": w.get("page"),
        "url": w.get("URL"),
        "publisher": w.get("publisher"),
    }
