#!/usr/bin/env python3
"""
Archive dataset chunker — indexes BIRLS, CA death, MO vital records,
NE death, NJ death into Oracle Object Storage using the same
PREFIX_DECADE chunk format as SSDI.

Oracle key pattern: {source}/{PREFIX}_{DECADE}.jsonl.gz
  e.g. birls/HEN_1910s.jsonl.gz
       ca_death/SMI_1940s.jsonl.gz
       mo_birth/JOH_1950s.jsonl.gz

Usage:
  python scripts/build_archive_chunks.py --source birls --upload
  python scripts/build_archive_chunks.py --source ca_death --upload
  python scripts/build_archive_chunks.py --all --upload
  python scripts/build_archive_chunks.py --source mo_birth --out-dir /tmp/chunks
"""

import argparse
import csv
import glob
import gzip
import io
import json
import logging
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import boto3
from botocore.config import Config

sys.path.insert(0, str(Path(__file__).parent.parent))
from app.chunk_crypto import encrypt

# ── Load .env ─────────────────────────────────────────────────────────────────
_env_path = Path(__file__).parent.parent / '.env'
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith('#') and '=' in _line:
            _k, _v = _line.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

# ── Oracle config ──────────────────────────────────────────────────────────────
BUCKET    = os.environ.get('ORACLE_BUCKET', 'rootbridge-data')
NAMESPACE = os.environ.get('ORACLE_NAMESPACE', '')
REGION    = os.environ.get('ORACLE_REGION', 'us-chicago-1')
ACCESS    = os.environ.get('ORACLE_ACCESS_KEY', '')
SECRET    = os.environ.get('ORACLE_SECRET_KEY', '')
ENDPOINT  = f'https://{NAMESPACE}.compat.objectstorage.{REGION}.oraclecloud.com'

ARCHIVE_BASE = Path('/mnt/h/RootBridge/archive_data')

# ── Helpers ────────────────────────────────────────────────────────────────────

def _prefix(last: str) -> str:
    clean = re.sub(r'[^A-Z]', '', (last or '').upper())
    return clean[:3].ljust(3, 'X') if clean else 'UNK'


def _decade(year_str) -> str:
    try:
        y = int(str(year_str)[:4])
        if 1700 <= y <= 2025:
            return f'{(y // 10) * 10}s'
    except (ValueError, TypeError):
        pass
    return 'unknown'


def _year_from_date(date_str: str) -> str | None:
    """Extract 4-digit year from various date formats."""
    if not date_str:
        return None
    m = re.search(r'\b(1[6-9]\d{2}|20[0-2]\d)\b', str(date_str))
    return m.group(1) if m else None


def _parse_iso_year(dob: str) -> str | None:
    """Parse YYYY-MM-DD → YYYY."""
    if not dob:
        return None
    m = re.match(r'(\d{4})-\d{2}-\d{2}', dob.strip())
    return m.group(1) if m else _year_from_date(dob)


# ── Dataset parsers ────────────────────────────────────────────────────────────

def iter_birls(csv_path: Path):
    """
    BIRLS Veterans Death Records (9M+ rows).
    Columns: SSN (strip), GENDER, DOB, DOD, CAUSE_OF_DEATH,
             BRANCH_OF_SERVICE_1, BRANCH_NAME_1, ..., LAST_NM, FIRST_NM, MIDDLE_NM, SUFFIX
    """
    with open(csv_path, encoding='utf-8', errors='replace') as f:
        reader = csv.DictReader(f)
        for row in reader:
            last  = (row.get('LAST_NM') or '').strip()
            first = (row.get('FIRST_NM') or '').strip()
            if not last:
                continue
            birth_year = _parse_iso_year(row.get('DOB', ''))
            death_year = _parse_iso_year(row.get('DOD', ''))
            yield {
                'source':       'birls',
                'record_type':  'death',
                'last':         last,
                'first':        first,
                'middle':       (row.get('MIDDLE_NM') or '').strip(),
                'suffix':       (row.get('SUFFIX') or '').strip(),
                'gender':       (row.get('GENDER') or '').strip(),
                'birth_year':   birth_year,
                'death_year':   death_year,
                'branch':       (row.get('BRANCH_NAME_1') or '').strip(),
            }


def iter_ca_death(csv_path: Path):
    """
    California Death Index (yearly CSVs, 1940–1997).
    Columns: LAST NAME, FIRST, MIDDLE, SEX, BIRTH DATE, DEATH DATE,
             BIRTH PLACE, DEATH PLACE, SSN (strip), MOTHERS, FATHER
    """
    with open(csv_path, encoding='utf-8', errors='replace') as f:
        reader = csv.DictReader(f)
        for row in reader:
            last  = (row.get('LAST NAME') or '').strip()
            first = (row.get('FIRST') or '').strip()
            if not last:
                continue
            yield {
                'source':       'ca_death',
                'record_type':  'death',
                'last':         last,
                'first':        first,
                'middle':       (row.get('MIDDLE') or '').strip(),
                'gender':       (row.get('SEX') or '').strip(),
                'birth_year':   _year_from_date(row.get('BIRTH DATE', '')),
                'death_year':   _year_from_date(row.get('DEATH DATE', '')),
                'birth_place':  (row.get('BIRTH PLACE') or '').strip(),
                'death_place':  (row.get('DEATH PLACE') or '').strip(),
                'mother_last':  (row.get('MOTHERS') or '').strip(),
                'father_last':  (row.get('FATHER') or '').strip(),
            }


def iter_mo_death(csv_path: Path):
    """
    Missouri Death Records.
    Columns: First Name, Middle, Last Name, Death Date
    (may vary by file — use flexible DictReader)
    """
    with open(csv_path, encoding='utf-8', errors='replace') as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        first_col = next((h for h in headers if 'first' in h.lower()), None)
        last_col  = next((h for h in headers if 'last' in h.lower()), None)
        date_col  = next((h for h in headers if 'death' in h.lower() and 'date' in h.lower()), None)
        mid_col   = next((h for h in headers if 'mid' in h.lower()), None)
        if not last_col:
            return
        for row in reader:
            last  = (row.get(last_col) or '').strip()
            first = (row.get(first_col or '') or '').strip()
            if not last:
                continue
            yield {
                'source':      'mo_death',
                'record_type': 'death',
                'last':        last,
                'first':       first,
                'middle':      (row.get(mid_col or '') or '').strip() if mid_col else '',
                'death_year':  _year_from_date(row.get(date_col or '', '')),
            }


def iter_mo_birth(csv_path: Path):
    """
    Missouri Birth Records.
    Columns: FIRST NAME, MIDDLE NAME, LAST NAME, DATE OF BIRTH
    """
    with open(csv_path, encoding='utf-8', errors='replace') as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        first_col = next((h for h in headers if 'first' in h.lower()), None)
        last_col  = next((h for h in headers if 'last' in h.lower()), None)
        date_col  = next((h for h in headers if 'birth' in h.lower() and 'date' in h.lower()), None)
        mid_col   = next((h for h in headers if 'mid' in h.lower()), None)
        if not last_col:
            return
        for row in reader:
            last  = (row.get(last_col) or '').strip()
            first = (row.get(first_col or '') or '').strip()
            if not last:
                continue
            yield {
                'source':      'mo_birth',
                'record_type': 'birth',
                'last':        last,
                'first':       first,
                'middle':      (row.get(mid_col or '') or '').strip() if mid_col else '',
                'birth_year':  _year_from_date(row.get(date_col or '', '')),
            }


def iter_ne_death(csv_path: Path):
    """
    Nebraska Death Records.
    Columns: State File #, County, First Name, Middle, Last Name, Death Date
    """
    with open(csv_path, encoding='utf-8', errors='replace') as f:
        reader = csv.DictReader(f)
        for row in reader:
            last  = (row.get('Last Name') or '').strip()
            first = (row.get('First Name') or '').strip()
            if not last:
                continue
            yield {
                'source':      'ne_death',
                'record_type': 'death',
                'last':        last,
                'first':       first,
                'middle':      (row.get('Middle') or '').strip(),
                'death_year':  _year_from_date(row.get('Death Date', '')),
                'county':      (row.get('County') or '').strip(),
            }


def iter_nj_death(csv_path: Path):
    """
    New Jersey Death Records.
    Columns: FirstName, LastName, MiddleName, StateFileNumber, DateOfDeath, DateOfBirth
    """
    with open(csv_path, encoding='utf-8', errors='replace') as f:
        reader = csv.DictReader(f)
        for row in reader:
            last  = (row.get('LastName') or '').strip()
            first = (row.get('FirstName') or '').strip()
            if not last:
                continue
            yield {
                'source':      'nj_death',
                'record_type': 'death',
                'last':        last,
                'first':       first,
                'middle':      (row.get('MiddleName') or '').strip(),
                'birth_year':  _year_from_date(row.get('DateOfBirth', '')),
                'death_year':  _year_from_date(row.get('DateOfDeath', '')),
            }


# ── Dataset registry ───────────────────────────────────────────────────────────

SOURCES = {
    'birls': {
        'desc':    'BIRLS Veterans Death Records',
        'files':   sorted(ARCHIVE_BASE.glob('BIRLS_veterans*.csv')),
        'iter':    iter_birls,
        'key_field': 'last',
        'year_field': 'birth_year',
    },
    'ca_death': {
        'desc':    'California Death Index 1940-1997',
        'files':   sorted((ARCHIVE_BASE / 'CA_death').glob('CA_death_*.csv')),
        'iter':    iter_ca_death,
        'key_field': 'last',
        'year_field': 'death_year',
    },
    'mo_death': {
        'desc':    'Missouri Death Records',
        'files':   sorted(ARCHIVE_BASE.glob('MO_death_*.csv')),
        'iter':    iter_mo_death,
        'key_field': 'last',
        'year_field': 'death_year',
    },
    'mo_birth': {
        'desc':    'Missouri Birth Records',
        'files':   sorted(ARCHIVE_BASE.glob('MO_birth_*.csv')),
        'iter':    iter_mo_birth,
        'key_field': 'last',
        'year_field': 'birth_year',
    },
    'ne_death': {
        'desc':    'Nebraska Death Records',
        'files':   sorted(ARCHIVE_BASE.glob('NE_death_*.csv')),
        'iter':    iter_ne_death,
        'key_field': 'last',
        'year_field': 'death_year',
    },
    'nj_death': {
        'desc':    'New Jersey Death Records',
        'files':   sorted(ARCHIVE_BASE.glob('NJ_death_*.csv')),
        'iter':    iter_nj_death,
        'key_field': 'last',
        'year_field': 'death_year',
    },
}


# ── Chunking ───────────────────────────────────────────────────────────────────

def chunk_source(source_name: str, row_limit: int = 0) -> dict:
    """Process all files for a source into PREFIX_DECADE chunks."""
    cfg = SOURCES[source_name]
    chunks: dict[str, list] = defaultdict(list)
    total = 0

    for csv_path in cfg['files']:
        logger.info('  Reading %s', csv_path.name)
        try:
            for i, rec in enumerate(cfg['iter'](csv_path)):
                last = rec.get(cfg['key_field'], '')
                year = rec.get(cfg['year_field'])
                key  = f"{source_name}/{_prefix(last)}_{_decade(year)}.jsonl.gz"
                chunks[key].append(rec)
                total += 1
                if row_limit and total >= row_limit:
                    logger.info('  Row limit %d reached', row_limit)
                    return dict(chunks)
                if total % 500_000 == 0:
                    logger.info('  %s: %d records, %d chunks', source_name, total, len(chunks))
        except Exception as e:
            logger.error('  Error reading %s: %s', csv_path.name, e)

    logger.info('%s: %d total records → %d chunks', source_name, total, len(chunks))
    return dict(chunks)


# ── Oracle upload (same as build_ssdi_chunks.py) ──────────────────────────────

def _get_oracle_client():
    if not NAMESPACE or not ACCESS:
        return None
    return boto3.client(
        's3',
        endpoint_url=ENDPOINT,
        aws_access_key_id=ACCESS,
        aws_secret_access_key=SECRET,
        config=Config(signature_version='s3v4', retries={'max_attempts': 3}, request_checksum_calculation='when_required'),
        region_name=REGION,
    )


def upload_chunks(chunks: dict, client) -> tuple[int, int]:
    uploaded = failed = 0
    for key, records in chunks.items():
        buf = io.BytesIO()
        with gzip.open(buf, 'wt', encoding='utf-8') as gz:
            for r in records:
                gz.write(json.dumps(r) + '\n')
        body = encrypt(buf.getvalue())
        for attempt in range(3):
            try:
                client.put_object(
                    Bucket=BUCKET,
                    Key=key,
                    Body=body,
                    ContentType='application/octet-stream',
                    ContentLength=len(body),
                )
                uploaded += 1
                break
            except Exception as e:
                if attempt == 2:
                    logger.warning('Upload failed %s: %s', key, e)
                    failed += 1
                else:
                    time.sleep(2 ** attempt)
    return uploaded, failed


def save_local(chunks: dict, out_dir: str):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for key, records in chunks.items():
        path = out / key.replace('/', '_')
        buf = io.BytesIO()
        with gzip.open(buf, 'wt', encoding='utf-8') as gz:
            for r in records:
                gz.write(json.dumps(r) + '\n')
        path.write_bytes(buf.getvalue())
    logger.info('Saved %d chunks to %s', len(chunks), out_dir)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description='Archive dataset chunker for Oracle upload')
    ap.add_argument('--source',    choices=list(SOURCES), help='Which dataset to process')
    ap.add_argument('--all',       action='store_true', help='Process all datasets sequentially')
    ap.add_argument('--upload',    action='store_true', help='Upload to Oracle (default: local only)')
    ap.add_argument('--out-dir',   default='/mnt/h/RootBridge/archive_chunks', help='Local output dir')
    ap.add_argument('--row-limit', type=int, default=0, help='Max rows per source (0=all) — for testing')
    ap.add_argument('--list',      action='store_true', help='List available sources and file counts')
    args = ap.parse_args()

    if args.list:
        for name, cfg in SOURCES.items():
            print(f'{name:12s}  {len(cfg["files"]):4d} files  {cfg["desc"]}')
        return

    if not args.source and not args.all:
        ap.print_help()
        return

    client = _get_oracle_client() if args.upload else None
    if args.upload and not client:
        logger.error('Oracle credentials not configured — check .env')
        return

    to_run = list(SOURCES) if args.all else [args.source]

    for source_name in to_run:
        logger.info('=== %s ===', SOURCES[source_name]['desc'])
        chunks = chunk_source(source_name, row_limit=args.row_limit)
        if not chunks:
            logger.warning('No records found for %s', source_name)
            continue

        if args.upload:
            up, fail = upload_chunks(chunks, client)
            logger.info('Oracle: %d uploaded, %d failed', up, fail)
        else:
            save_local(chunks, args.out_dir)

    logger.info('DONE.')


if __name__ == '__main__':
    main()
