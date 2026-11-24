"""A professional's HSA-id and legitimation (#1054, record.pdhc stage 2).

A NEW table, created by scripts/init_db.py (`create_all`, additive), and NOT
three new columns on `professionals`. The same reasoning as
`patient_invite_details` (#1050) and `second_factor_reenrolments` (#1045):
this repo has no Alembic, `create_all` creates missing TABLES only, and a new
column on a deployed table needs a migration script the operator must run
BEFORE the code. A new table needs only the usual init_db.

At most one row per professional (unique `professional_guid`). No row reads
as "nothing recorded": `hsa_id` null, `legitimation` null, not verified.

  hsa_id                 the person's HSA-id, upper-case, unique when set.
                         Format and the pilot's synthetic rule (D8) are in
                         services/professional_qualification.py.
  legitimation           a code from Socialstyrelsen's list of the 22
                         legitimationsyrken (same module), or null (no
                         legitimation recorded, e.g. undersköterska).
  legitimation_verified  FALSE. Nothing in the pilot sets it true: there is
                         no lookup against Socialstyrelsen's register (HOSP).
                         It is reset to false whenever `legitimation` changes.
"""
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String

from src.db import Base


def _utcnow():
    return datetime.now(timezone.utc)


class ProfessionalQualification(Base):
    __tablename__ = 'professional_qualifications'

    id = Column(Integer, primary_key=True)
    professional_guid = Column(String(36), ForeignKey('professionals.guid', ondelete='CASCADE'),
                               unique=True, nullable=False)
    hsa_id = Column(String(31), unique=True, nullable=True)
    legitimation = Column(String(40), nullable=True)
    legitimation_verified = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)
    updated_by_user_guid = Column(String(36), nullable=True)
