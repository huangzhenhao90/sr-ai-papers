"""
Unpaywall connector：按 DOI 查 OA 状态 + PDF 链接。
免费、需 mailto，无 token。
"""

import os
import time
from src.utils.http import make_client, get_with_retry

UPW_BASE = "https://api.unpaywall.org/v2/"
RATE_SLEEP = 0.15  # 100k req/day 上限


def fetch_one(doi: str, contact: str | None = None) -> dict | None:
    contact = contact or os.getenv("CONTACT_EMAIL", "anonymous@example.com")
    url = UPW_BASE + doi
    with make_client() as client:
        try:
            r = get_with_retry(client, url, params={"email": contact})
            return r.json()
        except Exception:
            return None
        finally:
            time.sleep(RATE_SLEEP)


def fetch_many(dois: list[str], contact: str | None = None) -> dict[str, dict]:
    """逐个查询。返回 {doi -> upw_record}。"""
    contact = contact or os.getenv("CONTACT_EMAIL", "anonymous@example.com")
    out = {}
    with make_client() as client:
        for i, doi in enumerate(dois, 1):
            try:
                r = get_with_retry(client, UPW_BASE + doi, params={"email": contact})
                out[doi] = r.json()
            except Exception:
                pass
            if i % 100 == 0:
                print(f"    Unpaywall 进度 {i}/{len(dois)}")
            time.sleep(RATE_SLEEP)
    return out
