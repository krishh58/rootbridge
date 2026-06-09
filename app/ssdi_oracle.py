"""
Oracle Object Storage SSDI query layer.

Chunks are stored as:  ssdi/{PREFIX}_{DECADE}.jsonl.gz
  PREFIX = first 3 chars of last name, uppercased, padded with X (e.g. HEN, SMI, JOX)
  DECADE = birth decade string (e.g. 1900s, 1910s, unknown)

Query strategy:
  1. Compute primary chunk key from last + birth_year
  2. Also fetch adjacent decade chunk (±10 years) to handle ±5yr birth year fuzz
  3. Stream-decompress each chunk, filter records by name + year
  4. Cache chunks in /tmp/ssdi_cache/ so repeated searches are instant
"""

import gzip
import io
import json
import logging
import os
import threading
from pathlib import Path

import boto3
from botocore.config import Config
from .chunk_crypto import decrypt

logger = logging.getLogger(__name__)

# ── Oracle credentials (loaded from env / .env at app startup) ────────────────
BUCKET    = os.environ.get('ORACLE_BUCKET', 'rootbridge-data')
NAMESPACE = os.environ.get('ORACLE_NAMESPACE', '')
REGION    = os.environ.get('ORACLE_REGION', 'us-chicago-1')
ACCESS    = os.environ.get('ORACLE_ACCESS_KEY', '')
SECRET    = os.environ.get('ORACLE_SECRET_KEY', '')
ENDPOINT  = f'https://{NAMESPACE}.compat.objectstorage.{REGION}.oraclecloud.com'

CACHE_DIR = Path('/tmp/ssdi_cache')
CACHE_DIR.mkdir(exist_ok=True)

_client_lock = threading.Lock()
_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is None:
            if not NAMESPACE or not ACCESS:
                return None
            _client = boto3.client(
                's3',
                endpoint_url=ENDPOINT,
                aws_access_key_id=ACCESS,
                aws_secret_access_key=SECRET,
                config=Config(signature_version='s3v4', retries={'max_attempts': 3}),
                region_name=REGION,
            )
    return _client


# ── Key computation (must match build_ssdi_chunks.py exactly) ─────────────────

def _prefix(last: str) -> str:
    clean = (last or '').upper().strip()
    return clean[:3].ljust(3, 'X') if clean else 'UNK'


def _decade_key(year) -> str:
    try:
        y = int(str(year)[:4])
        return f'{(y // 10) * 10}s'
    except (ValueError, TypeError):
        return 'unknown'


def _chunk_key(last: str, birth_year) -> str:
    return f'ssdi/{_prefix(last)}_{_decade_key(birth_year)}.jsonl.gz'


def _adjacent_chunk_key(last: str, birth_year) -> str | None:
    """Return chunk key for the adjacent decade if birth_year is within 5 of a decade boundary."""
    try:
        y = int(str(birth_year)[:4])
        decade = (y // 10) * 10
        if y - decade <= 4:          # within 4 years of start → also check prior decade
            adj = decade - 10
        elif decade + 10 - y <= 5:   # within 5 years of end → also check next decade
            adj = decade + 10
        else:
            return None
        return f'ssdi/{_prefix(last)}_{adj}s.jsonl.gz'
    except (ValueError, TypeError):
        return None


# ── Chunk fetching with local disk cache ──────────────────────────────────────

def _fetch_chunk(key: str) -> list[dict]:
    """Download and decompress a chunk from Oracle. Returns list of records."""
    cache_file = CACHE_DIR / key.replace('/', '_')

    # Serve from disk cache if available
    if cache_file.exists():
        try:
            with gzip.open(cache_file, 'rt', encoding='utf-8') as gz:
                return [json.loads(line) for line in gz if line.strip()]
        except Exception:
            cache_file.unlink(missing_ok=True)

    client = _get_client()
    if client is None:
        return []

    try:
        resp = client.get_object(Bucket=BUCKET, Key=key)
        raw = resp['Body'].read()
        body = decrypt(raw)
        # Write decrypted bytes to cache
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(body)
        # Parse
        with gzip.open(io.BytesIO(body), 'rt', encoding='utf-8') as gz:
            return [json.loads(line) for line in gz if line.strip()]
    except client.exceptions.NoSuchKey:
        return []
    except Exception as e:
        logger.warning('SSDI Oracle fetch failed for %s: %s', key, e)
        return []


# ── Name matching helpers ─────────────────────────────────────────────────────

def _name_match(rec_first: str, rec_last: str,
                query_first: str, query_last: str) -> bool:
    """Fuzzy name match — last must match exactly (prefix), first within edit distance 1."""
    rl = rec_last.upper()
    ql = query_last.upper().strip()
    if not rl.startswith(ql[:3]):   # prefix must match (guaranteed by chunk selection)
        return False
    # Exact last name match
    if rl != ql:
        return False
    # First name: exact, prefix, or first-char match
    if not query_first:
        return True
    rf = rec_first.upper().strip()
    qf = query_first.upper().strip().split()[0]  # use first word only
    if rf == qf:
        return True
    if rf.startswith(qf) or qf.startswith(rf):
        return True
    # Require at least 2-char prefix match (single-char too loose for full names)
    # But allow single-char if the record has an initial only (1-2 char field)
    if rf and qf:
        if len(rf) <= 2:
            if rf[0] == qf[0]:
                return True
        else:
            if rf[:3] == qf[:3]:
                return True
    return False


# ── Public search function ────────────────────────────────────────────────────

def search_ssdi(first: str, last: str, birth_year=None,
                birth_place: str = '', limit: int = 10) -> list[dict]:
    """
    Search SSDI Oracle chunks for a person.
    Returns list of result dicts compatible with search_cascade result format.
    """
    if not last:
        return []

    # Collect chunk keys to fetch
    keys = set()
    if birth_year:
        keys.add(_chunk_key(last, birth_year))
        adj = _adjacent_chunk_key(last, birth_year)
        if adj:
            keys.add(adj)
    else:
        # No birth year — try last few decades as a broad sweep
        for decade in range(1880, 1980, 10):
            keys.add(_chunk_key(last, decade))

    results = []
    seen = set()

    for key in keys:
        records = _fetch_chunk(key)
        for rec in records:
            rec_last  = rec.get('last', '')
            rec_first = rec.get('first', '')

            if not _name_match(rec_first, rec_last, first, last):
                continue

            # Year filter: allow ±8 years around stated birth year
            if birth_year and rec.get('birth_year'):
                try:
                    if abs(int(rec['birth_year']) - int(birth_year)) > 8:
                        continue
                except (TypeError, ValueError):
                    pass

            # Dedup by (first, last, birth_year, death_year)
            uid = (rec_last, rec_first, rec.get('birth_year'), rec.get('death_year'))
            if uid in seen:
                continue
            seen.add(uid)

            # Build state label
            state = rec.get('state', '')
            place_parts = [p for p in [state] if p]
            place_str   = ', '.join(place_parts) if place_parts else ''

            # Birth place filter — loose match on state abbreviation
            if birth_place and state:
                bp_up = birth_place.upper()
                if state not in bp_up and state.lower() not in birth_place.lower():
                    # Don't hard-reject — SSDI state is state-of-issue, not birth state
                    pass

            results.append({
                'source':      'ssdi',
                'record_type': 'death_record',
                'title':       f'{rec_first} {rec_last}'.strip(),
                'name':        f'{rec_first} {rec_last}'.strip(),
                'first_name':  rec_first,
                'last_name':   rec_last,
                'birth_year':  rec.get('birth_year'),
                'death_year':  rec.get('death_year'),
                'birth_place': place_str,
                'location':    place_str,
                'state':       state,
                'url':         '',
                'snippet':     (f"SSDI — born {rec.get('birth_year','?')}, "
                                f"died {rec.get('death_year','?')}"
                                + (f", {state}" if state else '')),
            })

            if len(results) >= limit:
                break

        if len(results) >= limit:
            break

    return results


def ssdi_available() -> bool:
    """Return True if Oracle is configured and the bucket appears to have data."""
    client = _get_client()
    if client is None:
        return False
    try:
        resp = client.list_objects_v2(Bucket=BUCKET, Prefix='ssdi/', MaxKeys=1)
        return resp.get('KeyCount', 0) > 0
    except Exception:
        return False
