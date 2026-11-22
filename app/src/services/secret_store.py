"""How #1040's secrets are kept at rest. One module, so the choice is in one place.

What the repo already did, and what was followed:

  * passwords            bcrypt (auth_service) — a low-entropy secret a human chose
  * read-grant secrets   sha256 (routes/read_grants.py) — 256 random bits, where
                         a fast hash is the right tool

Neither fits everything here, so two functions:

`keyed_hash(purpose, value)` — HMAC-SHA256 under a key derived from SECRET_KEY.
  Used for the patient-invite token and the link code. The invite token is 256
  random bits and plain sha256 would do; the LINK CODE is short (it is read out
  to a care unit), about 40 bits, and a plain hash of 40 bits can be searched
  offline by anyone holding a database dump. Keying it means a dump alone is
  not enough. It stays deterministic, so a code is found by one indexed lookup.

`encrypt` / `decrypt` — Fernet (AES-128-CBC + HMAC-SHA256, from `cryptography`).
  Used for the TOTP shared secret, which CANNOT be hashed: the server has to
  compute the current code from it. There was no encryption-at-rest pattern in
  this repo before; this is the first.

Both keys are DERIVED from SECRET_KEY with HKDF and a per-purpose label, so no
new variable is needed in `.env`. The consequence, stated: rotating SECRET_KEY
makes enrolled TOTP secrets unreadable (those patients enrol again; passkeys
are unaffected, they hold no server-side secret) and invalidates outstanding
invites and link codes. Rotating SECRET_KEY already logs everybody out, so it
is not a quiet operation today either.

Values carry a version prefix (`v1:`) so a later change of scheme can tell old
rows from new ones.
"""
import base64
import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_VERSION = 'v1'


class SecretStoreError(Exception):
    """SECRET_KEY missing, or a stored value that cannot be read."""


def _derive(secret_key, label):
    if not secret_key:
        raise SecretStoreError('SECRET_KEY is not set')
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                info=f'sso.pdhc/{label}/{_VERSION}'.encode()
                ).derive(secret_key.encode())


def keyed_hash(secret_key, purpose, value):
    """Hex HMAC-SHA256 of `value` under the key for `purpose`. 64 chars."""
    key = _derive(secret_key, f'hash/{purpose}')
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()


def encrypt(secret_key, purpose, plaintext):
    f = Fernet(base64.urlsafe_b64encode(_derive(secret_key, f'enc/{purpose}')))
    return f'{_VERSION}:' + f.encrypt(plaintext.encode()).decode()


def decrypt(secret_key, purpose, stored):
    prefix = f'{_VERSION}:'
    if not stored or not stored.startswith(prefix):
        raise SecretStoreError('unknown secret format')
    f = Fernet(base64.urlsafe_b64encode(_derive(secret_key, f'enc/{purpose}')))
    try:
        return f.decrypt(stored[len(prefix):].encode()).decode()
    except InvalidToken:
        raise SecretStoreError('secret cannot be decrypted (SECRET_KEY changed?)')
