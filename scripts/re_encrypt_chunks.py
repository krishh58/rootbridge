"""
Re-encrypt all existing Oracle Object Storage chunks.

Run once after setting ORACLE_ENCRYPT_KEY to migrate the existing
unencrypted chunks. Safe to interrupt and resume — skips chunks that
are already valid encrypted blobs.

Usage:
    python scripts/re_encrypt_chunks.py [--dry-run] [--prefix ssdi]
"""

import argparse
import io
import gzip
import json
import logging
import os
import sys
import time
from pathlib import Path

import boto3
from botocore.config import Config
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))
from app.chunk_crypto import encrypt, decrypt, _get_fernet

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
logger = logging.getLogger(__name__)

BUCKET    = os.environ.get('ORACLE_BUCKET', 'rootbridge-data')
NAMESPACE = os.environ.get('ORACLE_NAMESPACE', '')
REGION    = os.environ.get('ORACLE_REGION', 'us-chicago-1')
ACCESS    = os.environ.get('ORACLE_ACCESS_KEY', '')
SECRET    = os.environ.get('ORACLE_SECRET_KEY', '')
ENDPOINT  = f'https://{NAMESPACE}.compat.objectstorage.{REGION}.oraclecloud.com'


def get_client():
    return boto3.client(
        's3',
        endpoint_url=ENDPOINT,
        aws_access_key_id=ACCESS,
        aws_secret_access_key=SECRET,
        config=Config(signature_version='s3v4', request_checksum_calculation='when_required'),
        region_name=REGION,
    )


def is_already_encrypted(data: bytes) -> bool:
    """Fernet tokens start with 'gAAAAA' (base64 of version byte 0x80)."""
    return data[:6] == b'gAAAAA'


def re_encrypt_all(prefix_filter: str | None, dry_run: bool):
    if not _get_fernet():
        logger.error('ORACLE_ENCRYPT_KEY not set — aborting')
        sys.exit(1)

    client = get_client()
    paginator = client.get_paginator('list_objects_v2')

    kwargs = {'Bucket': BUCKET}
    if prefix_filter:
        kwargs['Prefix'] = prefix_filter.rstrip('/') + '/'

    total = already_enc = re_encrypted = failed = 0

    for page in paginator.paginate(**kwargs):
        for obj in page.get('Contents', []):
            key = obj['Key']
            total += 1

            try:
                resp = client.get_object(Bucket=BUCKET, Key=key)
                raw = resp['Body'].read()

                if is_already_encrypted(raw):
                    already_enc += 1
                    continue

                # Validate it's valid gzip before re-encrypting
                try:
                    with gzip.open(io.BytesIO(raw), 'rt') as gz:
                        gz.read(100)
                except Exception:
                    logger.warning('  SKIP %s — not valid gzip', key)
                    continue

                encrypted = encrypt(raw)

                if dry_run:
                    logger.info('[dry-run] would re-encrypt %s (%d → %d bytes)', key, len(raw), len(encrypted))
                    re_encrypted += 1
                    continue

                client.put_object(
                    Bucket=BUCKET,
                    Key=key,
                    Body=encrypted,
                    ContentType='application/octet-stream',
                    ContentLength=len(encrypted),
                )
                re_encrypted += 1

                if re_encrypted % 500 == 0:
                    logger.info('Progress: %d re-encrypted, %d already done, %d failed of %d seen',
                                re_encrypted, already_enc, failed, total)

            except Exception as e:
                logger.warning('  FAILED %s: %s', key, e)
                failed += 1
                time.sleep(1)

    logger.info('Done. Total: %d | Re-encrypted: %d | Already encrypted: %d | Failed: %d',
                total, re_encrypted, already_enc, failed)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--prefix', help='Only re-encrypt this prefix (e.g. ssdi, birls)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    re_encrypt_all(args.prefix, args.dry_run)


if __name__ == '__main__':
    main()
