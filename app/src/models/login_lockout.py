"""Failed-password state per account (#1040). One row per user that has ever
failed a password login; NEW table, created by scripts/init_db.py.

In the database, not in memory: gunicorn runs two workers, and a counter kept
per process would let an attacker have twice the attempts and would forget
them at every restart.
"""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String

from src.db import Base


class LoginLockout(Base):
    __tablename__ = 'login_lockouts'

    id = Column(Integer, primary_key=True)
    user_guid = Column(String(36), ForeignKey('users.guid', ondelete='CASCADE'),
                       unique=True, nullable=False)
    failed_count = Column(Integer, nullable=False, default=0)
    last_failed_at = Column(DateTime, nullable=True)
    locked_until = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False,
                        default=lambda: datetime.now(timezone.utc))
