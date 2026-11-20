import sqlite3
from pathlib import Path

db = Path(__file__).resolve().parent.parent / "history.sqlite"
c = sqlite3.connect(db)
tables = c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print("tables:", tables)
rows = c.execute("SELECT id, created_at, kind, file_name, status FROM runs ORDER BY id DESC LIMIT 20").fetchall()
for r in rows:
    print(r)
