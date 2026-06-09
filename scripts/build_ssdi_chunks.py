#!/usr/bin/env python3
"""
Build SSDI chunks from a raw SSDI text/CSV file and upload to Oracle Object Storage.

SSDI raw format (pipe-delimited or CSV):
  SSN | Last | First | Middle | DOB | DOD | State-of-issue | State-of-last-residence

Usage:
  python scripts/build_ssdi_chunks.py --input ssdi_raw.txt --upload
  python scripts/build_ssdi_chunks.py --input ssdi_raw.txt --out-dir ./chunks  (local only)

Free SSDI sources:
  - https://archive.org/search?query=social+security+death+index  (search for bulk download)
  - NARA Death Master File (requires request to SSA)
"""

import argparse
import csv
import gc
import gzip
import io
import json
import os
import sys
import time
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / '.env')
except ImportError:
    pass
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from app.chunk_crypto import encrypt

import boto3
from botocore.config import Config

# ── Load .env from project root if present ───────────────────────────────────
_env_path = Path(__file__).parent.parent / '.env'
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith('#') and '=' in _line:
            _k, _v = _line.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# ── Oracle config (from env) ─────────────────────────────────────────────────
BUCKET    = os.environ.get('ORACLE_BUCKET', 'rootbridge-data')
NAMESPACE = os.environ.get('ORACLE_NAMESPACE', '')
REGION    = os.environ.get('ORACLE_REGION', 'us-ashburn-1')
ACCESS    = os.environ.get('ORACLE_ACCESS_KEY', '')
SECRET    = os.environ.get('ORACLE_SECRET_KEY', '')
ENDPOINT  = f'https://{NAMESPACE}.compat.objectstorage.{REGION}.oraclecloud.com'

STATE_ABBR = {
    'AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA',
    'KS','KY','LA','ME','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ',
    'NM','NY','NC','ND','OH','OK','OR','PA','RI','SC','SD','TN','TX','UT','VT',
    'VA','WA','WV','WI','WY','DC',
}


def _decade_key(year_str: str) -> str:
    try:
        y = int(str(year_str)[:4])
        return f'{(y // 10) * 10}s'
    except (ValueError, TypeError):
        return 'unknown'


def _prefix(last: str) -> str:
    clean = (last or '').upper().strip()
    return clean[:3].ljust(3, 'X') if clean else 'UNK'


# Numeric SSA state-of-issue codes → 2-letter abbreviation (approx)
_SSA_STATE = {
    '01':'NH','02':'ME','03':'VT','04':'MA','05':'RI','06':'CT',
    '07':'NY','08':'NJ','09':'PA','10':'PA','11':'PA','12':'PA',
    '13':'PA','14':'PA','15':'PA','16':'PA','17':'PA','18':'PA',
    '19':'PA','20':'PA','21':'PA','22':'DE','23':'MD','24':'DC',
    '25':'VA','26':'NC','27':'SC','28':'GA','29':'FL','30':'OH',
    '31':'IN','32':'IL','33':'MI','34':'WI','35':'MN','36':'IA',
    '37':'MO','38':'ND','39':'SD','40':'NE','41':'KS','42':'DE',
    '43':'OK','44':'TX','45':'CO','46':'NM','47':'AZ','48':'UT',
    '49':'NV','50':'WA','51':'OR','52':'CA','53':'CA','54':'CA',
    '55':'CA','56':'CA','57':'CA','58':'CA','59':'HI','60':'AK',
    '61':'MT','62':'ID','63':'WY','64':'CO','65':'NM','66':'TX',
    '67':'TX','68':'TX','69':'TX','70':'TX','71':'TX','72':'TX',
    '73':'OK','74':'OK','75':'TX','76':'TX','77':'TX','78':'TX',
    '79':'AR','80':'MS','81':'TN','82':'KY','83':'AL','84':'WV',
    '85':'VA','86':'NC','87':'SC','88':'GA','89':'FL','90':'PA',
    '91':'TN','92':'MO','93':'MO','94':'WI','95':'IL','96':'MI',
    '98':'VI','99':'PR',
}


def parse_record_fixed(line: str) -> dict | None:
    """Parse one fixed-width SSDI line.
    SSA Death Master File actual layout (0-based, confirmed from raw data):
      [0]     verification type
      [1:10]  SSN (9 chars)
      [10:34] last name (24 chars)
      [34:65] first name (31 chars, right-padded)
      [65:73] DOD MMDDYYYY
      [73:81] DOB MMDDYYYY
      [81:83] state code (may be blank)
      [83:88] zip (may be blank)
    """
    if len(line) < 81:
        return None
    try:
        last  = line[10:34].strip().upper()
        first = (line[34:65].strip().split() or [''])[0].title()
        dod_str = line[65:73].strip()
        dob_str = line[73:81].strip()
        state_code = line[81:83].strip() if len(line) >= 83 else ''

        if not last:
            return None

        death_year = int(dod_str[4:8]) if len(dod_str) == 8 and dod_str[4:8] != '0000' else None
        birth_year = int(dob_str[4:8]) if len(dob_str) == 8 and dob_str[4:8] != '0000' else None

        if birth_year and not (1800 <= birth_year <= 2010):
            return None

        return {
            'first': first,
            'last':  last,
            'birth_year': birth_year,
            'death_year': death_year,
            'state': _SSA_STATE.get(state_code, ''),
        }
    except (ValueError, TypeError):
        return None


RAM_LIMIT_GB = 8.0  # pause if system available RAM drops below this


def _available_ram_gb() -> float:
    try:
        with open('/proc/meminfo') as f:
            for line in f:
                if line.startswith('MemAvailable:'):
                    return int(line.split()[1]) / (1024 ** 2)
    except Exception:
        pass
    return 99.0


def _wait_for_ram(threshold_gb: float = RAM_LIMIT_GB):
    while True:
        avail = _available_ram_gb()
        if avail >= threshold_gb:
            break
        print(f'  [RAM] only {avail:.1f} GB available — waiting 30s…', flush=True)
        time.sleep(30)


def _make_client():
    return boto3.client(
        's3',
        endpoint_url=ENDPOINT,
        aws_access_key_id=ACCESS,
        aws_secret_access_key=SECRET,
        config=Config(signature_version='s3v4', request_checksum_calculation='when_required'),
        region_name=REGION,
    )


def _existing_keys(client) -> set:
    """Return set of chunk keys already in Oracle ssdi/ prefix."""
    existing = set()
    paginator = client.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=BUCKET, Prefix='ssdi/'):
        for obj in page.get('Contents', []):
            existing.add(obj['Key'])
    print(f'  {len(existing):,} chunks already in Oracle — will skip those', flush=True)
    return existing


def _upload_chunks(client, chunks: dict, existing: set) -> tuple[int, int]:
    """Upload a dict of {key: [records]} to Oracle. Returns (uploaded, failed)."""
    uploaded = 0
    failed = 0
    skipped = 0
    for key, records in chunks.items():
        if key in existing:
            skipped += 1
            continue
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
                    print(f'  WARN: failed {key} after 3 attempts: {e}')
                    failed += 1
                else:
                    import time as _time; _time.sleep(2 ** attempt)
    return uploaded, failed


def upload_oracle_streaming(input_path: str):
    """Stream SSDI file one first-letter prefix at a time to cap RAM at ~400MB."""
    if not NAMESPACE or not ACCESS:
        print('ERROR: Set ORACLE_NAMESPACE, ORACLE_ACCESS_KEY, ORACLE_SECRET_KEY env vars')
        sys.exit(1)

    client = _make_client()
    existing = _existing_keys(client)

    # 26 letters + fallback bucket for non-alpha (numbers, punctuation)
    letter_buckets = list('ABCDEFGHIJKLMNOPQRSTUVWXYZ') + ['_OTHER']
    total_uploaded = 0
    total_failed = 0
    total_records = 0

    for letter in letter_buckets:
        chunks: dict[str, list] = defaultdict(list)
        count = 0

        with open(input_path, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                rec = parse_record_fixed(line)
                if rec is None:
                    continue
                first_char = rec['last'][0] if rec['last'] else '_'
                if letter == '_OTHER':
                    if first_char.isalpha():
                        continue
                else:
                    if first_char != letter:
                        continue
                key = f"ssdi/{_prefix(rec['last'])}_{_decade_key(str(rec['birth_year']))}.jsonl.gz"
                chunks[key].append(rec)
                count += 1

        if not chunks:
            continue

        total_records += count
        _wait_for_ram()  # pause if system RAM is low before uploading
        print(f'[{letter}] {count:,} records → {len(chunks):,} chunks — uploading…', flush=True)
        up, fail = _upload_chunks(client, chunks, existing)
        total_uploaded += up
        total_failed += fail
        existing.update(chunks.keys())
        print(f'[{letter}] done: +{up} uploaded, {fail} failed. Total so far: {total_uploaded:,}', flush=True)
        del chunks
        gc.collect()
        time.sleep(1)  # brief pause between letters to let OS reclaim pages

    print(f'\nAll done: {total_records:,} records, {total_uploaded:,} chunks uploaded, {total_failed} failed')


def save_local_streaming(input_path: str, out_dir: str):
    """Write chunks to local files without holding everything in RAM."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    total = 0
    skipped = 0
    handles: dict[str, io.BytesIO] = {}
    gz_handles: dict[str, gzip.GzipFile] = {}

    with open(input_path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            rec = parse_record_fixed(line)
            if rec is None:
                skipped += 1
                continue
            key = f"ssdi/{_prefix(rec['last'])}_{_decade_key(str(rec['birth_year']))}.jsonl.gz"
            if key not in handles:
                handles[key] = open(out / key.replace('/', '_'), 'wb')
                gz_handles[key] = gzip.open(handles[key], 'wt', encoding='utf-8')
            gz_handles[key].write(json.dumps(rec) + '\n')
            total += 1
            if total % 1_000_000 == 0:
                print(f'  {total:,} records written…', flush=True)

    for gz in gz_handles.values():
        gz.close()
    for fh in handles.values():
        fh.close()
    print(f'Saved {len(handles)} chunk files to {out_dir} ({total:,} records, {skipped:,} skipped)')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', required=True, help='Path to raw SSDI text/CSV file')
    ap.add_argument('--upload', action='store_true', help='Upload to Oracle Object Storage')
    ap.add_argument('--out-dir', default='./ssdi_chunks', help='Local output dir (if not uploading)')
    args = ap.parse_args()

    if not os.path.exists(args.input):
        print(f'ERROR: Input file not found: {args.input}')
        sys.exit(1)

    print(f'Parsing {args.input}…')
    if args.upload:
        upload_oracle_streaming(args.input)
    else:
        save_local_streaming(args.input, args.out_dir)


if __name__ == '__main__':
    main()
