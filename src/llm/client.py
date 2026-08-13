"""
MiniMax-M3 LLM client (OpenAI 兼容接口)。

注意：M3 是 thinking model，每次调用会消耗大量 reasoning tokens。
为降低单篇成本，业务层应使用「批量打分」（一次喂多篇）。
"""

import os
import json
import time
import re
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type


class MiniMaxClient:
    def __init__(self):
        self.api_key = os.environ["MINIMAX_API_KEY"]
        self.base_url = os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1")
        self.model = os.environ.get("MINIMAX_MODEL", "MiniMax-M3")
        self.client = httpx.Client(
            timeout=180,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=100),
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=2, max=30),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        reraise=True,
    )
    def chat(self, messages: list[dict], max_tokens: int = 2000, temperature: float = 0.1) -> dict:
        """返回完整 response dict。失败抛异常。"""
        url = f"{self.base_url}/text/chatcompletion_v2"
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        r = self.client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
        # MiniMax 失败时 status_code != 0
        base = data.get("base_resp") or {}
        if base.get("status_code") not in (0, None):
            raise RuntimeError(f"MiniMax API error: {base}")
        return data

    def chat_text(self, messages: list[dict], max_tokens: int = 2000, temperature: float = 0.1) -> str:
        """只返回 content 字符串。"""
        data = self.chat(messages, max_tokens=max_tokens, temperature=temperature)
        choices = data.get("choices") or []
        if not choices:
            return ""
        msg = choices[0].get("message") or {}
        return msg.get("content") or ""

    def usage(self, data: dict) -> dict:
        return data.get("usage") or {}

    def close(self):
        self.client.close()


# ---------- 工具：从输出文本里提取 JSON ----------
_JSON_FENCE = re.compile(r"```(?:json)?\s*", re.IGNORECASE)


def extract_json(text: str):
    """从 LLM 输出抽 JSON，容忍 ```json fence、首尾杂字符、截断的数组。"""
    if not text:
        return None

    # 去掉 ```json 开头和 ``` 结尾的 fence
    cleaned = _JSON_FENCE.sub("", text).strip()
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()

    # 找最外层 [ 或 {
    if cleaned.startswith("["):
        return _parse_array(cleaned)
    if cleaned.startswith("{"):
        try:
            return json.loads(cleaned)
        except Exception:
            try:
                return json.loads(re.sub(r",(\s*[}\]])", r"\1", cleaned))
            except Exception:
                return None

    # 在文本里搜
    i = cleaned.find("[")
    if i >= 0:
        return _parse_array(cleaned[i:])
    i = cleaned.find("{")
    if i >= 0:
        try:
            return json.loads(cleaned[i:])
        except Exception:
            return None
    return None


def _parse_array(text: str):
    """对 [{},{},{}] 形式的数组，先整体解析；失败则逐个对象解析（兼容截断）。"""
    text = text.strip()
    # 整体先试
    try:
        return json.loads(text)
    except Exception:
        pass
    fixed = _fix_bare_json(text)
    try:
        return json.loads(fixed)
    except Exception:
        pass
    cleaned = re.sub(r",(\s*[}\]])", r"\1", text)
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    try:
        return json.loads(_fix_bare_json(cleaned))
    except Exception:
        pass

    # 逐个对象抠出来——用括号深度扫描
    out = []
    depth = 0
    start = None
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"' and not esc:
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                obj_str = text[start : i + 1]
                try:
                    out.append(json.loads(obj_str))
                except Exception:
                    try:
                        out.append(json.loads(re.sub(r",(\s*[}\]])", r"\1", obj_str)))
                    except Exception:
                        try:
                            out.append(json.loads(_fix_bare_json(obj_str)))
                        except Exception:
                            pass
                start = None
    return out if out else None


def _fix_bare_json(text: str):
    """容错：MiniMax 偶发输出 {"id":p2,"ai":5} 这种裸标识符值，把裸值补上引号。"""
    # 值侧："key":bareword -> "key":"bareword"（不碰已带引号的值和数字）
    text = re.sub(
        r'("\s*:\s*)([A-Za-z_][A-Za-z0-9_.\-]*)(\s*[,}\]])',
        r'\1"\2"\3',
        text,
    )
    # key 侧：{bareword: -> {"bareword":
    text = re.sub(
        r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)",
        r'\1"\2"\3',
        text,
    )
    return text
