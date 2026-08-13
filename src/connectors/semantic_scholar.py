"""
Semantic Scholar connector：按 DOI 批量补摘要 + 引用数。

API: https://api.semanticscholar.org/graph/v1/paper/batch
- 一次最多 500 个 ID
- 无 key 限速 5000/5min（足够用）
- 有 key (S2_API_KEY) 限速更高
"""

import os
import time
from src.utils.http import make_client, get_with_retry

S2_BATCH = "https://api.semanticscholar.org/graph/v1/paper/batch"
S2_FIELDS = "abstract,title,citationCount,influentialCitationCount,year,openAccessPdf,externalIds"
BATCH_SIZE = 100         # 大 batch 容易 429，缩小
RATE_SLEEP_OK = 1.2      # 正常成功后等待
RATE_SLEEP_429 = 30      # 429 后退避
MAX_RETRIES = 4


def fetch_by_dois(dois: list[str]) -> dict[str, dict]:
    """输入 DOI 列表 -> 返回 {doi -> 字段 dict}（缺的 DOI 不在字典里）。"""
    api_key = os.getenv("S2_API_KEY")
    headers = {}
    if api_key:
        headers["x-api-key"] = api_key

    out = {}
    with make_client(timeout=60) as client:
        for i in range(0, len(dois), BATCH_SIZE):
            batch = dois[i : i + BATCH_SIZE]
            ids = [f"DOI:{d}" for d in batch]

            retry = 0
            while retry < MAX_RETRIES:
                try:
                    r = client.post(
                        S2_BATCH,
                        params={"fields": S2_FIELDS},
                        json={"ids": ids},
                        headers=headers,
                        timeout=60,
                    )
                    if r.status_code == 429:
                        wait = RATE_SLEEP_429 * (retry + 1)
                        print(f"    429 退避 {wait}s (retry {retry+1}/{MAX_RETRIES})")
                        time.sleep(wait)
                        retry += 1
                        continue
                    r.raise_for_status()
                    data = r.json()
                    for doi, item in zip(batch, data):
                        if item:
                            out[doi] = item
                    break
                except Exception as e:
                    print(f"  ! batch {i//BATCH_SIZE+1} 错误: {e}")
                    retry += 1
                    time.sleep(RATE_SLEEP_429)
            time.sleep(RATE_SLEEP_OK)
    return out
