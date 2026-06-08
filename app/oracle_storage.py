"""
Oracle Cloud Object Storage client for RootBridge census chunks.

Chunks are stored as gzipped JSONL files named by surname prefix + birth decade:
  ssdi/{prefix}_{decade}s.jsonl.gz   e.g. ssdi/HEN_1900s.jsonl.gz

Each line in the JSONL is one person record:
  {"first":"Mary","last":"Henderson","birth_year":1902,"death_year":1959,"state":"MI","ssn_last4":""}
"""

import gzip
import io
import json
import os
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from .chunk_crypto import decrypt

_BUCKET = os.environ.get('ORACLE_BUCKET', 'rootbridge-data')
_NAMESPACE = os.environ.get('ORACLE_NAMESPACE', '')      # your tenancy namespace
_REGION = os.environ.get('ORACLE_REGION', 'us-chicago-1')
_ACCESS_KEY = os.environ.get('ORACLE_ACCESS_KEY', '')
_SECRET_KEY = os.environ.get('ORACLE_SECRET_KEY', '')

_ENDPOINT = f'https://{_NAMESPACE}.compat.objectstorage.{_REGION}.oraclecloud.com'

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client(
            's3',
            endpoint_url=_ENDPOINT,
            aws_access_key_id=_ACCESS_KEY,
            aws_secret_access_key=_SECRET_KEY,
            config=Config(signature_version='s3v4'),
            region_name=_REGION,
        )
    return _client


def _chunk_key(last: str, birth_year: int | None, dataset: str = 'ssdi') -> str:
    prefix = (last[:3].upper() if last else 'UNK').ljust(3, 'X')
    decade = f'{(birth_year // 10) * 10}s' if birth_year else 'unknown'
    return f'{dataset}/{prefix}_{decade}.jsonl.gz'


def fetch_chunk(key: str) -> list[dict]:
    """Download and decompress a single chunk. Returns [] on miss or error."""
    try:
        obj = _get_client().get_object(Bucket=_BUCKET, Key=key)
        raw = decrypt(obj['Body'].read())
        with gzip.open(io.BytesIO(raw), 'rt', encoding='utf-8') as f:
            return [json.loads(line) for line in f if line.strip()]
    except ClientError as e:
        if e.response['Error']['Code'] in ('NoSuchKey', '404'):
            return []
        raise
    except Exception:
        return []


def search_ssdi(first: str, last: str, birth_year: int | None,
                birth_place: str = '') -> list[dict]:
    """
    Search SSDI chunks for a person. Checks the decade-exact chunk and
    adjacent decade chunks (±10 years) to handle birth year uncertainty.
    """
    if not last:
        return []

    keys_to_try = set()
    if birth_year:
        for adj in (-10, 0, 10):
            keys_to_try.add(_chunk_key(last, birth_year + adj, 'ssdi'))
    else:
        keys_to_try.add(_chunk_key(last, None, 'ssdi'))

    first_lower = first.lower()
    last_lower = last.lower()
    state_hint = birth_place.lower() if birth_place else ''

    matches = []
    for key in keys_to_try:
        for record in fetch_chunk(key):
            if record.get('last', '').lower() != last_lower:
                continue
            rec_first = record.get('first', '').lower()
            if first_lower and rec_first and not (
                rec_first.startswith(first_lower[:3]) or
                first_lower.startswith(rec_first[:3])
            ):
                continue
            if state_hint and record.get('state', '').lower():
                if record['state'].lower() not in state_hint and \
                   state_hint not in record['state'].lower():
                    continue
            matches.append({
                'source': 'ssdi',
                'record_type': 'death_record',
                'title': f"{record.get('first','')} {record.get('last','')}".strip(),
                'birth_year': record.get('birth_year'),
                'death_year': record.get('death_year'),
                'birth_place': record.get('state', ''),
                'death_place': record.get('death_state', ''),
                'url': '',
            })
            if len(matches) >= 10:
                break

    return matches


def chunk_is_available() -> bool:
    """Quick check — can we reach Oracle Storage at all?"""
    if not _ACCESS_KEY or not _NAMESPACE:
        return False
    try:
        _get_client().head_bucket(Bucket=_BUCKET)
        return True
    except Exception:
        return False
