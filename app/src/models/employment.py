"""Employment — a person is EMPLOYED by one or more vårdgivare.

Operator's main rule (2026-11-13): a vårdperson is employed by a
vårdorganisation (vårdgivare) and AFFILIATED to a number of vårdenheter; the
vårdenhet is what lets them see patients in the request phase (request,
gatekeeper, CDR1, dashboard).

Before this there was no employment at all: the legacy UserOrganisation
membership ("+ Org" in the SU panel) stood in for it, mixed caregivers and
units, and was labelled "affiliation" in the UI although it grants no patient
access once a person has any real Affiliation. Two users were "added to Sim"
that way on 2026-11-13 and still could not see a Sim patient.

Decisions (operator 2026-11-13):
  - a person may be employed by ONE OR MORE vårdgivare (this table);
  - an Affiliation may ONLY target a care unit under a vårdgivare the person
    is employed by (enforced in the admin route; see employment_service).

`caregiver_guid` is always a CareOrganisation: an internal organisations row
with no parent. A vårdgivare with no units is its own unit for affiliation
purposes (careunit := caregiver).
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint

from src.db import Base


def _utcnow():
    return datetime.now(timezone.utc)


class Employment(Base):
    __tablename__ = 'employments'

    id = Column(Integer, primary_key=True)
    guid = Column(String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    person_guid = Column(String(36), ForeignKey('users.guid'), nullable=False, index=True)
    caregiver_guid = Column(String(36), ForeignKey('organisations.guid'), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    created_by_guid = Column(String(36), nullable=True)

    __table_args__ = (
        UniqueConstraint('person_guid', 'caregiver_guid', name='uq_employment_person_caregiver'),
    )

    def to_dict(self, caregiver_name=None):
        return {'employment_guid': self.guid, 'person_guid': self.person_guid,
                'caregiver_guid': self.caregiver_guid, 'caregiver_name': caregiver_name,
                'created_at': self.created_at.isoformat() if self.created_at else None}
