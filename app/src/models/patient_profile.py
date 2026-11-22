"""Patient name (#1049): what a self-registered patient called themselves.

A NEW table (created by scripts/init_db.py, nothing altered): `patients` is
deployed and `create_all` does not add columns to an existing table, so the
names live beside it rather than in it. One row per patient account.

Only the name is kept, for the blob's `display_name`. The mobile number the
form captures goes to eps (the patient register) and is NOT stored here: SSO
has no use for it, so it holds no copy.
"""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String

from src.db import Base


class PatientProfile(Base):
    __tablename__ = 'patient_profiles'

    id = Column(Integer, primary_key=True)
    user_guid = Column(String(36), ForeignKey('users.guid', ondelete='CASCADE'),
                       unique=True, nullable=False)
    given_name = Column(String(100), nullable=False)
    family_name = Column(String(100), nullable=False)
    created_at = Column(DateTime, nullable=False,
                        default=lambda: datetime.now(timezone.utc))
