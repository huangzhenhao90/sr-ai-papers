"""
OpenAlex connector: 按 source_id + 日期范围全量抓取期刊文章。

策略：
- 不用关键词过滤（召回反转：期刊全量 → 后置 AI 判定）
- 用 cursor 分页（OpenAlex 推荐方式）
- per-page 200 是上限
- 每次抓取写一条 SourceRun 日志，原始记录写 raw_records
"""

import os
import time
from datetime import datetime
from typing import Iterator

from src.utils.http import make_client, get_with_retry

OPENALEX_WORKS = "https://api.openalex.org/works"
PER_PAGE = 200
RATE_SLEEP = 0.15  # 200 req/s 上限远超此值


def fetch_works_by_source(
    source_id: str,
    from_date: str,
    to_date: str | None = None,
    contact: str | None = None,
) -> Iterator[dict]:
    """生成器：逐条产出某 source_id 在日期范围内的所有 works。"""
    contact = contact or os.getenv("CONTACT_EMAIL", "anonymous@example.com")

    filters = [f"primary_location.source.id:{source_id}"]
    filters.append(f"from_publication_date:{from_date}")
    if to_date:
        filters.append(f"to_publication_date:{to_date}")

    cursor = "*"
    with make_client() as client:
        while cursor:
            params = {
                "filter": ",".join(filters),
                "per-page": PER_PAGE,
                "cursor": cursor,
                "mailto": contact,
            }
            r = get_with_retry(client, OPENALEX_WORKS, params=params)
            data = r.json()
            for w in data.get("results", []):
                yield w
            cursor = data.get("meta", {}).get("next_cursor")
            time.sleep(RATE_SLEEP)


def reconstruct_abstract(inv_idx: dict | None) -> str | None:
    """OpenAlex 摘要存的是 inverted index，要还原成正常文本。"""
    if not inv_idx:
        return None
    positions = []
    for word, idxs in inv_idx.items():
        for i in idxs:
            positions.append((i, word))
    positions.sort()
    return " ".join(w for _, w in positions)


def slim_record(w: dict) -> dict:
    """裁剪 OpenAlex 原始记录，去掉超大字段（concepts/related_works 等），只留入库需要的。"""
    primary_loc = w.get("primary_location") or {}
    source = primary_loc.get("source") or {}
    biblio = w.get("biblio") or {}
    best_oa = w.get("best_oa_location") or {}

    return {
        "id": w.get("id"),
        "doi": w.get("doi"),
        "title": w.get("title"),
        "publication_year": w.get("publication_year"),
        "publication_date": w.get("publication_date"),
        "type": w.get("type"),
        "language": w.get("language"),
        "cited_by_count": w.get("cited_by_count"),
        "is_retracted": w.get("is_retracted"),
        "abstract": reconstruct_abstract(w.get("abstract_inverted_index")),
        "authorships": [
            {
                "name": (a.get("author") or {}).get("display_name"),
                "orcid": (a.get("author") or {}).get("orcid"),
                "affiliations": [i.get("display_name") for i in (a.get("institutions") or [])],
                "position": a.get("author_position"),
            }
            for a in (w.get("authorships") or [])
        ],
        "source": {
            "id": source.get("id"),
            "display_name": source.get("display_name"),
            "issn_l": source.get("issn_l"),
        },
        "biblio": {
            "volume": biblio.get("volume"),
            "issue": biblio.get("issue"),
            "first_page": biblio.get("first_page"),
            "last_page": biblio.get("last_page"),
        },
        "open_access": {
            "is_oa": (w.get("open_access") or {}).get("is_oa"),
            "oa_url": (w.get("open_access") or {}).get("oa_url"),
        },
        "best_oa_url": best_oa.get("pdf_url") or best_oa.get("landing_page_url"),
        "landing_page_url": primary_loc.get("landing_page_url"),
        "pdf_url": primary_loc.get("pdf_url"),
    }
