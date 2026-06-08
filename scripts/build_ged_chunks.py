#!/usr/bin/env python3
"""
Build Oracle chunks from ged_vault_index.db for Alfred's routing layer.

Chunks are stored as:  ged/{PREFIX}_{DECADE}.jsonl.gz
  PREFIX = first 3 chars of last name, uppercased, padded with X
  DECADE = birth decade (1900s, 1910s, unknown)

Each record includes source_file so Alfred can route the AI to the right GED file.

Usage:
  python3 scripts/build_ged_chunks.py [--upload] [--dry-run]
"""

import argparse
import gzip
import io
import json
import logging
import os
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
from app.chunk_crypto import encrypt

load_dotenv(Path(__file__).parent.parent / '.env')

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

DB_PATH    = Path(__file__).parent.parent / 'data' / 'ged_vault_index.db'
BUCKET     = os.environ.get('ORACLE_BUCKET', 'rootbridge-data')
NAMESPACE  = os.environ.get('ORACLE_NAMESPACE', '')
REGION     = os.environ.get('ORACLE_REGION', 'us-chicago-1')
ACCESS     = os.environ.get('ORACLE_ACCESS_KEY', '')
SECRET     = os.environ.get('ORACLE_SECRET_KEY', '')
ENDPOINT   = f'https://{NAMESPACE}.compat.objectstorage.{REGION}.oraclecloud.com'

# GED files live on H drive — Alfred reads from here, not C drive
GED_H_BASE = Path('/mnt/h/RootBridge/ged_files')
# Old WSL path prefix to rewrite
_WSL_PREFIX = str(Path(__file__).parent.parent) + '/'


def _get_client():
    return boto3.client(
        's3',
        endpoint_url=ENDPOINT,
        aws_access_key_id=ACCESS,
        aws_secret_access_key=SECRET,
        config=Config(
            signature_version='s3v4',
            retries={'max_attempts': 3},
            request_checksum_calculation='when_required',
        ),
        region_name=REGION,
    )


def _prefix(last: str) -> str:
    clean = (last or '').upper().strip()
    return clean[:3].ljust(3, 'X') if clean else 'UNK'


def _decade(year) -> str:
    try:
        y = int(year)
        return f'{(y // 10) * 10}s'
    except (TypeError, ValueError):
        return 'unknown'


def _rel_path(abs_path: str) -> str:
    """Convert any source_file path to H drive absolute path."""
    if not abs_path:
        return ''
    filename = Path(abs_path).name
    return str(GED_H_BASE / filename)


def load_records() -> dict[str, list[dict]]:
    """
    Read ged_persons from SQLite, group into chunks keyed by PREFIX_DECADE.
    Skips test/fixture persons (last_name = 'TESTER' or no last name).
    """
    logger.info('Reading from %s', DB_PATH)
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    cur = db.cursor()

    chunks: dict[str, list[dict]] = defaultdict(list)
    total = 0
    skipped = 0

    cur.execute("""
        SELECT first_name, last_name, birth_year, death_year,
               birth_place, death_place, gender, file_hash, indi_id, source_file
        FROM   ged_persons
        WHERE  last_name IS NOT NULL
          AND  last_name != ''
          AND  last_name != 'TESTER'
    """)

    for row in cur:
        last  = (row['last_name'] or '').strip().upper()
        first = (row['first_name'] or '').strip()
        if not last or len(last) < 2:
            skipped += 1
            continue

        key = f'ged/{_prefix(last)}_{_decade(row["birth_year"])}.jsonl.gz'
        chunks[key].append({
            'first':       first,
            'last':        last,
            'birth_year':  row['birth_year'],
            'death_year':  row['death_year'],
            'birth_place': row['birth_place'] or '',
            'death_place': row['death_place'] or '',
            'gender':      row['gender'] or '',
            'file_hash':   row['file_hash'] or '',
            'indi_id':     row['indi_id'] or '',
            'source_file': _rel_path(row['source_file']),
        })
        total += 1
        if total % 100_000 == 0:
            logger.info('  loaded %d records, %d chunks so far', total, len(chunks))

    db.close()
    logger.info('Total: %d records → %d chunks (%d skipped)', total, len(chunks), skipped)
    return dict(chunks)


def compress_chunk(records: list[dict]) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode='wb') as gz:
        for rec in records:
            gz.write((json.dumps(rec) + '\n').encode('utf-8'))
    return buf.getvalue()


def upload_chunks(chunks: dict[str, list[dict]], dry_run: bool = False) -> tuple[int, int]:
    client = None if dry_run else _get_client()
    uploaded = 0
    failed = 0

    keys = sorted(chunks.keys())
    total = len(keys)

    for i, key in enumerate(keys, 1):
        records = chunks[key]
        body = encrypt(compress_chunk(records))

        if dry_run:
            logger.info('[dry-run] %s — %d records, %d bytes', key, len(records), len(body))
            uploaded += 1
            continue

        try:
            client.put_object(
                Bucket=BUCKET,
                Key=key,
                Body=body,
                ContentType='application/octet-stream',
                ContentLength=len(body),
            )
            uploaded += 1
            if uploaded % 200 == 0 or i == total:
                logger.info('  Uploaded %d/%d chunks (%d failed)', uploaded, total, failed)
        except Exception as e:
            logger.warning('  FAILED %s: %s', key, e)
            failed += 1

    return uploaded, failed


def main():
    ap = argparse.ArgumentParser(description='Build GED index chunks for Oracle (Alfred routing layer)')
    ap.add_argument('--upload',  action='store_true', help='Upload to Oracle after building')
    ap.add_argument('--dry-run', action='store_true', help='Build chunks but do not upload')
    args = ap.parse_args()

    chunks = load_records()

    if args.upload or args.dry_run:
        uploaded, failed = upload_chunks(chunks, dry_run=args.dry_run)
        logger.info('Oracle: %d uploaded, %d failed', uploaded, failed)
        if not args.dry_run:
            logger.info('DONE — ged/ prefix ready for Alfred routing')
    else:
        logger.info('Built %d chunks in memory — pass --upload to push to Oracle', len(chunks))


if __name__ == '__main__':
    main()
