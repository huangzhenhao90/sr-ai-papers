"""初始化 SQLite 数据库（建表）。"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
from src.db.schema import init_db

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "./data/papers.db")
db_path = Path(DB_PATH).resolve()
db_path.parent.mkdir(parents=True, exist_ok=True)

print(f"初始化数据库 -> {db_path}")
engine = init_db(str(db_path))
print("完成。表清单:")
for t in engine.dialect.get_table_names(engine.connect()):
    print(f"  - {t}")
