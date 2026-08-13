"""统一 HTTP 客户端：带 User-Agent、限速、重试。"""

import os
import time
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

CONTACT = os.getenv("CONTACT_EMAIL", "anonymous@example.com")
USER_AGENT = f"ur-ai-papers/0.1 (mailto:{CONTACT})"


def make_client(timeout: int = 30) -> httpx.Client:
    return httpx.Client(
        timeout=timeout,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )


@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
    reraise=True,
)
def get_with_retry(client: httpx.Client, url: str, params: dict | None = None) -> httpx.Response:
    r = client.get(url, params=params)
    if r.status_code == 429:
        # 触发限速，等更久
        time.sleep(10)
        r.raise_for_status()
    r.raise_for_status()
    return r
