"""Time-limited external READ grants on CDR data (2026-11-19).

An SU admin gives an EXTERNAL partner (an `Organisation` with is_external) a
key that, for at most 48 hours, lets it read de-identified patient data:

  * only patients belonging to the chosen organisations (`org_guids`, matched
    against the CDRs' `patient_org_guid`);
  * only in the chosen CDRs (`cdrs`, e.g. ["cdr2"]); each CDR refuses a grant
    that does not name it.

The key is shown once and stored as a hash. It is `rg.<grant guid>.<secret>`
so the grant is found by guid and only the secret is compared. A grant is
never edited: it expires or is revoked, and a new one is issued.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.types import JSON

from src.db import Base

#: The hard ceiling the operator set. Enforced at issue time, and again at
#: validation time so a row written by hand cannot outlive it.
MAX_HOURS = 48
#: The targets a grant may name: the three CDRs and ips (its own door,
#: 2026-11-19). cdr_6 runs other code and has no external door. The column
#: is still called `cdrs` -- it predates ips becoming a target.
KNOWN_CDRS = ("cdr1", "cdr2", "cdr3", "ips")
#: The consent purpose a grant reads under, chosen per key by the SU admin
#: (operator decision 2026-11-19). ips's consent_policy applies it: research
#: needs the patient's consent to one of the grant's research projects; the
#: other three exclude EHDS opt-outs (quality_registry also its own opt-out).
PURPOSES = ("research", "statistics", "quality_assurance", "quality_registry")
KEY_PREFIX = "rg"


def _utcnow():
    return datetime.now(timezone.utc)


def _new_guid():
    return str(uuid.uuid4())


class ReadGrant(Base):
    __tablename__ = 'read_grants'

    id = Column(Integer, primary_key=True)
    guid = Column(String(36), unique=True, nullable=False, default=_new_guid)
    partner_guid = Column(String(36), nullable=False, index=True)   # Organisation.guid, is_external
    org_guids = Column(JSON, nullable=False)        # patient organisations covered
    cdrs = Column(JSON, nullable=False)             # e.g. ["cdr2", "cdr3"]
    secret_hash = Column(String(255), nullable=False)
    purpose = Column(String(500), nullable=True)          # free text, recorded
    purpose_code = Column(String(32), nullable=True)      # one of PURPOSES
    research_project_guids = Column(JSON, nullable=True)  # for purpose_code == research
    created_by_user_guid = Column(String(36), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoked_by_user_guid = Column(String(36), nullable=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)

    def status(self, now=None) -> str:
        now = now or _utcnow()
        if self.revoked_at:
            return 'revoked'
        exp = self.expires_at if self.expires_at.tzinfo else self.expires_at.replace(tzinfo=timezone.utc)
        return 'expired' if exp <= now else 'active'

    def to_dict(self):
        def iso(d):
            return d.isoformat() if d else None
        return {
            'guid': self.guid, 'partner_guid': self.partner_guid,
            'org_guids': list(self.org_guids or []), 'cdrs': list(self.cdrs or []),
            'purpose': self.purpose, 'purpose_code': self.purpose_code,
            'research_project_guids': list(self.research_project_guids or []),
            'status': self.status(),
            'created_by_user_guid': self.created_by_user_guid,
            'created_at': iso(self.created_at), 'expires_at': iso(self.expires_at),
            'revoked_at': iso(self.revoked_at), 'last_used_at': iso(self.last_used_at),
        }
