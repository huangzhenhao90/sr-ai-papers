"""
一键跑：26 本英文期刊全部抓取 + 规范化去重。
"""

import os
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
from src.utils.journals import english_journals
from src.pipeline.ingest_english import main as ingest_one
from src.pipeline.normalize import normalize

load_dotenv()
FROM_DATE = "2023-01-01"

journals = english_journals()
print(f"\n{'='*60}")
print(f"开始抓取 {len(journals)} 本英文期刊（{FROM_DATE} 至今）")
print(f"{'='*60}\n")

t0 = time.time()
for i, j in enumerate(journals, 1):
    print(f"\n>>> [{i}/{len(journals)}] {j['abbr']} ({j['name_en']})")
    try:
        ingest_one(j["abbr"], from_date=FROM_DATE)
    except Exception as e:
        print(f"!!! 失败: {e}")
        continue

t1 = time.time()
print(f"\n{'='*60}")
print(f"抓取阶段完成，用时 {(t1-t0)/60:.1f} 分钟")
print(f"{'='*60}\n")

print("\n开始规范化去重（全部期刊）…")
normalize(journal_abbr=None, only_new=True)

t2 = time.time()
print(f"\n全流程总用时 {(t2-t0)/60:.1f} 分钟")
