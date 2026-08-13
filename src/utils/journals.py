"""Journal registry: 加载 journals.yaml 并提供查询接口。"""

from pathlib import Path
from typing import Optional
import yaml


def load_journals(config_path: Optional[Path] = None) -> list[dict]:
    if config_path is None:
        config_path = Path(__file__).resolve().parents[2] / "config" / "journals.yaml"
    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("journals", [])


def by_abbr(abbr: str) -> Optional[dict]:
    for j in load_journals():
        if j["abbr"] == abbr:
            return j
    return None


def by_issn(issn: str) -> Optional[dict]:
    issn_norm = issn.replace("-", "").upper()
    for j in load_journals():
        for field in ("issn", "eissn"):
            v = (j.get(field) or "").replace("-", "").upper()
            if v and v == issn_norm:
                return j
    return None


def all_issns() -> list[tuple[str, str]]:
    """返回 [(abbr, issn), ...]，每本期刊每个 ISSN 一条。"""
    out = []
    for j in load_journals():
        for field in ("issn", "eissn"):
            v = j.get(field)
            if v:
                out.append((j["abbr"], v))
    return out


def english_journals() -> list[dict]:
    return [j for j in load_journals() if j.get("lang") == "en"]


def chinese_journals() -> list[dict]:
    return [j for j in load_journals() if j.get("lang") == "zh"]
