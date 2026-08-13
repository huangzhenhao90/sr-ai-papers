"""
为会议（无 ISSN）按 display_name 在 OpenAlex 搜 source_id，写回 journals.yaml。

策略:
  - 用 sources endpoint 的 search 参数匹配 display_name
  - 优先匹配 type=conference + works_count 高的
  - 若不到，降级取 works_count 最高的
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

# 顶会显示名 → OpenAlex 搜索关键词映射（更精准的名字）
CONFERENCE_SEARCH = {
    "CHI": "CHI Conference on Human Factors in Computing Systems",
    "CSCW": "Computer Supported Cooperative Work",
    "UIST": "User Interface Software and Technology",
    "DIS": "Designing Interactive Systems",
    "IUI": "Intelligent User Interfaces",
    "HRI": "Human-Robot Interaction",
    "MobileHCI": "Mobile Human Computer Interaction",
    "TEI": "Tangible Embedded and Embodied Interaction",
    "IEEEVIS": "IEEE Visualization",
    "INTERACT": "INTERACT Human Computer Interaction",
    "ICIS": "International Conference Information Systems",
    "HICSS": "Hawaii International Conference System Sciences",
}


def search_source(name: str) -> str | None:
    url = "https://api.openalex.org/sources"
    params = {"search": name, "mailto": CONTACT, "per-page": 10}
    headers = {"User-Agent": f"ur-ai-papers/0.1 ({CONTACT})"}
    try:
        r = httpx.get(url, params=params, headers=headers, timeout=20)
        r.raise_for_status()
        data = r.json()
        results = data.get("results", [])
        if not results:
            return None
        # 优先 type=conference
        confs = [x for x in results if x.get("type") == "conference"]
        pool = confs if confs else results
        best = max(pool, key=lambda x: x.get("works_count", 0))
        return best.get("id", "").replace("https://openalex.org/", "")
    except Exception as e:
        print(f"  ! ERROR: {e}")
        return None


def main():
    with open(CONFIG, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    updated = 0
    for j in data["journals"]:
        if j.get("type") != "conference":
            continue
        if j.get("openalex_source_id"):
            print(f"[skip] {j['abbr']} 已填")
            continue
        abbr = j["abbr"]
        query = CONFERENCE_SEARCH.get(abbr, j["name_en"])
        sid = search_source(query)
        if sid:
            j["openalex_source_id"] = sid
            updated += 1
            print(f"[ok]   {abbr:<10} <- {sid}  (q='{query}')")
        else:
            print(f"[miss] {abbr:<10}  (q='{query}')")
        time.sleep(0.3)

    if updated:
        with open(CONFIG, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, sort_keys=False, width=120)
        print(f"\n已更新 {updated} 个会议的 openalex_source_id")
    else:
        print("\n无更新")


if __name__ == "__main__":
    main()
