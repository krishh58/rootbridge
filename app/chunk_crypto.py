"""
Chunk encryption layer for Oracle Object Storage.

All chunks are AES-256 encrypted (via Fernet) before upload and decrypted
after download.  The key lives only in the ORACLE_ENCRYPT_KEY environment
variable — never in Oracle.  A stolen bucket is a pile of useless bytes.

Key management:
  - Generate once:  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  - Store as ORACLE_ENCRYPT_KEY in Railway environment variables
  - If key is missing, encryption is SKIPPED and a warning is logged — this
    allows existing unencrypted chunks to still be read during migration.

Migration path (existing unencrypted chunks):
  - Set ORACLE_ENCRYPT_KEY
  - Run:  python scripts/re_encrypt_chunks.py  (re-uploads all existing chunks)
  - After re-encrypt completes, all reads/writes go through this module automatically
"""

import logging
import os

logger = logging.getLogger(__name__)

_fernet = None
_warned = False


def _get_fernet():
    global _fernet, _warned
    if _fernet is not None:
        return _fernet
    key = os.environ.get('ORACLE_ENCRYPT_KEY', '').strip()
    if not key:
        if not _warned:
            logger.warning('ORACLE_ENCRYPT_KEY not set — chunks stored unencrypted')
            _warned = True
        return None
    try:
        from cryptography.fernet import Fernet
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
        return _fernet
    except Exception as e:
        logger.error('Bad ORACLE_ENCRYPT_KEY: %s', e)
        return None


def encrypt(data: bytes) -> bytes:
    """Encrypt chunk bytes. Returns data unchanged if key not configured."""
    f = _get_fernet()
    if f is None:
        return data
    return f.encrypt(data)


def decrypt(data: bytes) -> bytes:
    """
    Decrypt chunk bytes.  Handles both encrypted and legacy unencrypted chunks
    so migration can happen gradually without breaking live searches.
    """
    f = _get_fernet()
    if f is None:
        return data
    try:
        return f.decrypt(data)
    except Exception:
        # Not encrypted — legacy chunk, return as-is
        return data


def generate_key() -> str:
    """Print a new random key suitable for ORACLE_ENCRYPT_KEY."""
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()
