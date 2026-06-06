"""Post local SQLite vault to Railway in chunks via /api/admin/vault-import."""
import sqlite3
import json
import requests
import sys
import time
from pathlib import Path

RAILWAY_URL = "https://web-production-3add3.up.railway.app/api/admin/vault-import"
SEED_SECRET = "rootbridge2026vault"
CHUNK = 500
DB = Path(__file__).parent.parent / "instance" / "local.db"

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
c = conn.cursor()
c.execute("SELECT COUNT(*) FROM persons")
total = c.fetchone()[0]
print(f"Total persons: {total:,}")

c.execute("""
    SELECT first_name, last_name, middle_name, birth_year, birth_state,
           birth_country, death_year, death_place, notes, confidence,
           soundex_key, birth_decade
    FROM persons
""")

sent = 0
errors = 0
session = requests.Session()
session.headers.update({'X-Seed-Secret': SEED_SECRET, 'Content-Type': 'application/json'})

while True:
    rows = c.fetchmany(CHUNK)
    if not rows:
        break
    payload = [dict(r) for r in rows]
    for attempt in range(3):
        try:
            resp = session.post(RAILWAY_URL, data=json.dumps(payload), timeout=30)
            if resp.status_code == 200:
                sent += resp.json().get('inserted', len(payload))
                break
            else:
                print(f"\n[{resp.status_code}] {resp.text[:200]}")
                errors += 1
                break
        except Exception as e:
            print(f"\nRetry {attempt+1}: {e}")
            time.sleep(3)
    print(f"  {sent:,} / {total:,} sent  ({errors} errors)", end="\r", flush=True)

conn.close()
print(f"\nDone. Sent {sent:,} persons, {errors} errors.")
