"""What care told SSO about an invited patient (#1050).

A NEW table, created by scripts/init_db.py (`create_all`, additive). It is
NOT two new columns on `patient_invites`, on purpose: that table is deployed
(#1040), and `create_all` creates missing TABLES only -- it never adds a
column to an existing one. New columns would need a migration script the
operator runs before the code; a new table needs nothing but the usual
init_db, which is this repo's convention (it has no Alembic).

One row per invite made with a name (dashboard's "Bjud in patient" always
sends one; the older API callers need not):

  given_name / family_name  shown on the invite page and stored as the
                            account's `patient_profiles` row on acceptance
  pnr_failures              how many times a wrong personnummer was typed on
                            this invite. At `MAX_PNR_FAILURES` the invite is
                            closed (it reads as invalid, like a used one), so
                            the link cannot be used to guess the number.

No personnummer is stored here: the number the patient types is checked
against EPS (`eps_client.identifier_match`), the register that holds it.
"""
from sqlalchemy import Column, ForeignKey, Integer, String

from src.db import Base


class PatientInviteDetail(Base):
    __tablename__ = 'patient_invite_details'

    id = Column(Integer, primary_key=True)
    invite_guid = Column(String(36), ForeignKey('patient_invites.guid', ondelete='CASCADE'),
                         unique=True, nullable=False)
    given_name = Column(String(100), nullable=True)
    family_name = Column(String(100), nullable=True)
    pnr_failures = Column(Integer, nullable=False, default=0)
