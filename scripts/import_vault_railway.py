"""Run this inside Railway console: python3 scripts/import_vault_railway.py"""
import urllib.request
import gzip
import json
import sys

URL = "https://github.com/krishh58/rootbridge/releases/download/vault-seed-v1/vault_export.jsonl.gz"
DEST = "/tmp/vault.jsonl.gz"

print("Downloading vault export...")
import urllib.request
opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler())
with opener.open(URL) as r, open(DEST, "wb") as f:
    f.write(r.read())
print("Downloaded. Starting import...")

from app import create_app
from app.db import db
from app.models import Person

app = create_app()
with app.app_context():
    batch, total = [], 0
    with gzip.open(DEST, "rt", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            batch.append(Person(
                first_name=r.get("first_name"),
                last_name=r.get("last_name"),
                middle_name=r.get("middle_name"),
                birth_year=r.get("birth_year"),
                birth_state=r.get("birth_state"),
                birth_country=r.get("birth_country"),
                death_year=r.get("death_year"),
                death_place=r.get("death_place"),
                notes=r.get("notes"),
                confidence=r.get("confidence"),
                soundex_key=r.get("soundex_key"),
                birth_decade=r.get("birth_decade"),
            ))
            if len(batch) >= 1000:
                db.session.bulk_save_objects(batch)
                db.session.commit()
                total += len(batch)
                batch = []
                print(f"  {total:,} inserted", flush=True)
    if batch:
        db.session.bulk_save_objects(batch)
        db.session.commit()
        total += len(batch)

print(f"Done! {total:,} persons imported.")
