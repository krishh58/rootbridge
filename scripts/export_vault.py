"""Export local SQLite vault to gzipped JSONL for Railway seeding."""
import sqlite3
import json
import gzip
import sys
from pathlib import Path

DB = Path(__file__).parent.parent / "instance" / "local.db"
OUT = Path(__file__).parent.parent / "vault_export.jsonl.gz"

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
c = conn.cursor()

c.execute("SELECT COUNT(*) FROM persons")
total = c.fetchone()[0]
print(f"Exporting {total:,} persons...")

written = 0
with gzip.open(OUT, "wt", encoding="utf-8") as f:
    c.execute("""
        SELECT first_name, last_name, middle_name, birth_year, birth_state,
               birth_country, death_year, death_place, notes, confidence,
               soundex_key, birth_decade
        FROM persons
    """)
    while True:
        rows = c.fetchmany(5000)
        if not rows:
            break
        for row in rows:
            f.write(json.dumps(dict(row)) + "\n")
            written += 1
        print(f"  {written:,} / {total:,}", end="\r", flush=True)

conn.close()
size_mb = OUT.stat().st_size / 1024 / 1024
print(f"\nDone. Written {written:,} rows → {OUT} ({size_mb:.1f} MB)")
