"""Keyword registry."""

import re
from pathlib import Path
from typing import Optional
from functools import lru_cache
import yaml


def load_keywords(config_path: Optional[Path] = None) -> dict:
    if config_path is None:
        config_path = Path(__file__).resolve().parents[2] / "config" / "keywords.yaml"
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


# 仅含 ASCII 字母数字的短词（如 "AI", "LLM", "GPT-4"）需要词边界匹配，
# 否则 "AI" 会命中 "fail / explain / domain / brain"。
# 中文不需要词边界（CJK 不分词）。
_NEEDS_BOUNDARY = re.compile(r"^[A-Za-z0-9][\w\-]{0,8}$")


def _is_chinese(s: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in s)


@lru_cache(maxsize=None)
def _compile_pattern(keyword: str) -> re.Pattern:
    """根据关键词形态选择匹配方式：
    - 中文：直接子串
    - 短英文/含连字符：词边界
    - 长英文短语（含空格）：直接子串（不区分大小写）
    """
    if _is_chinese(keyword):
        return re.compile(re.escape(keyword))
    if " " in keyword:
        # 多词短语，直接 case-insensitive 子串
        return re.compile(re.escape(keyword), re.IGNORECASE)
    if _NEEDS_BOUNDARY.match(keyword):
        # 短词：用词边界，注意 "GPT-4" 这种含连字符的，\b 在 - 处会失效，
        # 所以左右都用 (?<![A-Za-z0-9])(?![A-Za-z0-9]) 自定义边界
        return re.compile(
            r"(?<![A-Za-z0-9])" + re.escape(keyword) + r"(?![A-Za-z0-9])",
            re.IGNORECASE,
        )
    return re.compile(re.escape(keyword), re.IGNORECASE)


def _hits_in(text: str, words: list[str]) -> int:
    return sum(1 for w in words if _compile_pattern(w).search(text))


def count_hits(text: str, lang: str = "en") -> dict:
    """统计三层关键词命中数。返回 {l1, l2, l3}。"""
    if not text:
        return {"l1": 0, "l2": 0, "l3": 0}
    kws = load_keywords()
    out = {}
    for layer_key, layer_name in [("ai_general", "l1"), ("human_ai", "l2"), ("genai_llm", "l3")]:
        words = kws.get(layer_key, {}).get(lang, [])
        out[layer_name] = _hits_in(text, words)
    return out


def matched_keywords(text: str, lang: str = "en") -> dict:
    """调试用：返回每层实际命中的关键词列表。"""
    if not text:
        return {"l1": [], "l2": [], "l3": []}
    kws = load_keywords()
    out = {}
    for layer_key, layer_name in [("ai_general", "l1"), ("human_ai", "l2"), ("genai_llm", "l3")]:
        words = kws.get(layer_key, {}).get(lang, [])
        out[layer_name] = [w for w in words if _compile_pattern(w).search(text)]
    return out
