"""The one-time re-enrolment code an SU reset issues (#1045).

A NEW table, created by scripts/init_db.py (`create_all`, additive), like
`patient_invite_details` (#1050): this repo has no Alembic, and `create_all`
creates missing TABLES only, so the state lives here rather than in new
columns on `users` or `totp_secrets`.

At most one row per user. While a row exists with `completed_at` NULL, the
account is "reset, not yet re-enrolled": a password login gets NO token and
the patient must enter the code before a new factor can be added
(services/second_factor.py, "re-enrolment after an SU reset").

  code_hash        keyed HMAC of "<user_guid>:<code>" (secret_store.keyed_hash).
                   The code itself is never stored, logged or audited.
  expires_at       issued + REENROL_CODE_TTL
  failed_attempts  wrong codes typed; incremented IN THE DATABASE. At
                   REENROL_MAX_FAILURES the code is void (`voided_at`).
  used_at          the right code was typed (single use). The session that
                   typed it may enrol; no other session can.
  completed_at     a new factor was enrolled; the gate is lifted.

A new reset deletes the row and writes a new one (new guid), so an earlier
code, and a session that used one, stop working.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String

from src.db import Base


def _utcnow():
    return datetime.now(timezone.utc)


def _new_guid():
    return str(uuid.uuid4())


class SecondFactorReenrolment(Base):
    __tablename__ = 'second_factor_reenrolments'

    id = Column(Integer, primary_key=True)
    guid = Column(String(36), unique=True, nullable=False, default=_new_guid)
    user_guid = Column(String(36), ForeignKey('users.guid', ondelete='CASCADE'),
                       unique=True, nullable=False)
    code_hash = Column(String(64), nullable=False)
    issued_by_user_guid = Column(String(36), nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    expires_at = Column(DateTime, nullable=False)
    failed_attempts = Column(Integer, nullable=False, default=0)
    voided_at = Column(DateTime, nullable=True)
    used_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
