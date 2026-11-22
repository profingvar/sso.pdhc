"""Second factor for patient accounts (#1040): passkeys and TOTP.

Two tables, both NEW (created by scripts/init_db.py, nothing altered):

  webauthn_credentials  one row per registered passkey. Holds the PUBLIC key
                        only; a passkey's private key never leaves the device,
                        so there is nothing secret in this table.
  totp_secrets          at most one row per user. The shared secret cannot be
                        hashed (the server must compute the code from it), so
                        it is stored ENCRYPTED: see services/secret_store.py.

`confirmed_at` on a TOTP row is what "enrolled" means: a secret the user has
not yet proven they hold (by entering one valid code) is not a second factor.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.types import JSON

from src.db import Base


def _utcnow():
    return datetime.now(timezone.utc)


def _new_guid():
    return str(uuid.uuid4())


class WebAuthnCredential(Base):
    __tablename__ = 'webauthn_credentials'

    id = Column(Integer, primary_key=True)
    guid = Column(String(36), unique=True, nullable=False, default=_new_guid)
    user_guid = Column(String(36), ForeignKey('users.guid', ondelete='CASCADE'),
                       nullable=False, index=True)
    # base64url, as the browser sends it. Unique across all users: the same
    # authenticator credential cannot be registered to two accounts.
    credential_id = Column(String(1024), unique=True, nullable=False)
    public_key = Column(Text, nullable=False)          # base64url COSE key
    sign_count = Column(Integer, nullable=False, default=0)
    transports = Column(JSON, nullable=True)
    name = Column(String(100), nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    last_used_at = Column(DateTime, nullable=True)


class TotpSecret(Base):
    __tablename__ = 'totp_secrets'

    id = Column(Integer, primary_key=True)
    guid = Column(String(36), unique=True, nullable=False, default=_new_guid)
    user_guid = Column(String(36), ForeignKey('users.guid', ondelete='CASCADE'),
                       unique=True, nullable=False)
    secret_enc = Column(Text, nullable=False)          # secret_store.encrypt()
    confirmed_at = Column(DateTime, nullable=True)     # NULL = not enrolled yet
    # Replay guard: the 30-second step of the last accepted code. A code for
    # this step or an earlier one is refused, so a code read over a shoulder
    # cannot be used a second time.
    last_used_step = Column(Integer, nullable=True)
    failed_attempts = Column(Integer, nullable=False, default=0)
    locked_until = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
