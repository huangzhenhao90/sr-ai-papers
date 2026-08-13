"""校验 journals.yaml 是否能正确加载，打印汇总。"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collections import Counter
from src.utils.journals import load_journals

journals = load_journals()
print(f"共加载 {len(journals)} 本期刊\n")

by_lang = Counter(j["lang"] for j in journals)
by_tier = Counter(j["tier"] for j in journals)
by_pub = Counter(j["publisher"] for j in journals)

print("按语言:", dict(by_lang))
print("按级别:", dict(by_tier))
print("按出版商:")
for k, v in by_pub.most_common():
    print(f"  {k}: {v}")

print("\n字段缺失检查:")
issues = []
for j in journals:
    if not j.get("issn"):
        issues.append(f"  [无 ISSN] {j['abbr']}")
    if j.get("lang") == "en" and not j.get("rss") and not j.get("publisher_toc"):
        issues.append(f"  [无 RSS 也无 TOC] {j['abbr']}")
if issues:
    print("\n".join(issues))
else:
    print("  无")

print("\n所有期刊 (abbr | name_en | issn):")
for j in journals:
    print(f"  {j['abbr']:<18} | {j['name_en']:<55} | {j['issn']}")
