"""
按 ISSN 查询 OpenAlex 的 sources endpoint，把 source_id 写回 journals.yaml。
仅对 lang=en 的期刊执行（中文期刊基本不在 OpenAlex 索引内）。
"""

import os
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import yaml
from dotenv import load_dotenv

load_dotenv()
CONTACT = os.getenv("CONTACT_EMAIL", "anonymous@example.com")
CONFIG = Path(__file__).resolve().parents[1] / "config" / "journals.yaml"


def query_openalex_source(issn: str) -> str | None:
    url = "https://api.openalex.org/sources"
    params = {"filter": f"issn:{issn}", "mailto": CONTACT}
    headers = {"User-Agent": f"ur-ai-papers/0.1 ({CONTACT})"}
    try:
        r = httpx.get(url, params=params, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        results = data.get("results", [])
        if not results:
            return None
        # 取 works_count 最大的（最像主刊）
        best = max(results, key=lambda x: x.get("works_count", 0))
        sid = best.get("id", "")
        return sid.replace("https://openalex.org/", "")
    except Exception as e:
        print(f"  ! ERROR querying {issn}: {e}")
        return None


def main():
    with open(CONFIG, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    updated = 0
    for j in data["journals"]:
        if j.get("lang") != "en":
            continue
        if j.get("openalex_source_id"):
            print(f"[skip] {j['abbr']} 已填: {j['openalex_source_id']}")
            continue

        issn = j["issn"]
        sid = query_openalex_source(issn)
        if sid:
            j["openalex_source_id"] = sid
            updated += 1
            print(f"[ok]   {j['abbr']:<18} <- {sid}")
        else:
            print(f"[miss] {j['abbr']:<18} ISSN={issn} 未找到")
        time.sleep(0.3)  # OpenAlex 友好限速

    if updated:
        with open(CONFIG, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, sort_keys=False, width=120)
        print(f"\n已更新 {updated} 本期刊的 openalex_source_id")
    else:
        print("\n无更新")


if __name__ == "__main__":
    main()
