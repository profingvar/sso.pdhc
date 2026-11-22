"""Patient link (#1040): which eps/ips patient an SSO patient account IS.

Four tables, all NEW (created by scripts/init_db.py, nothing altered):

  patient_links         the current link. ONE account <-> ONE patient, both
                        directions enforced by unique constraints, so a race
                        cannot produce two.
  patient_link_history  append-only. Every link, relink and unlink, with who
                        and when. The audit log is a FILE with 90-day
                        retention; who tied an account to a patient has to
                        outlive that, so it is also kept here.
  patient_invites       care invites a patient (entrance 1). The link token is
                        shown once and stored as a keyed hash.
  patient_link_codes    a self-registered patient's one-time code (entrance 2),
                        stored as a keyed hash.

This is NOT `models/invite.py`: that `Invite` is a GROUP-join token for
professionals and stores its token in clear. It was deliberately not reused.

`ips_patient_guid` is the patient GUID from eps.pdhc (formerly ips.pdhc) — the
`patient_guid` on ServiceRequests and CDR rows (Rule 18). SSO does not call
eps: it cannot check that the guid exists or that the patient is listed at the
care unit. The professional who links is the control, and is recorded.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String

from src.db import Base


def _utcnow():
    return datetime.now(timezone.utc)


def _new_guid():
    return str(uuid.uuid4())


class PatientLink(Base):
    __tablename__ = 'patient_links'

    id = Column(Integer, primary_key=True)
    guid = Column(String(36), unique=True, nullable=False, default=_new_guid)
    user_guid = Column(String(36), ForeignKey('users.guid', ondelete='CASCADE'),
                       unique=True, nullable=False)
    ips_patient_guid = Column(String(36), unique=True, nullable=False)
    care_unit_guid = Column(String(36), nullable=True)
    linked_by_user_guid = Column(String(36), nullable=False)
    linked_at = Column(DateTime, nullable=False, default=_utcnow)
    method = Column(String(16), nullable=False)   # invite | link_code | su_relink


class PatientLinkHistory(Base):
    __tablename__ = 'patient_link_history'

    id = Column(Integer, primary_key=True)
    guid = Column(String(36), unique=True, nullable=False, default=_new_guid)
    event = Column(String(16), nullable=False)    # link | relink | unlink
    user_guid = Column(String(36), nullable=False, index=True)
    ips_patient_guid = Column(String(36), nullable=True)
    previous_ips_patient_guid = Column(String(36), nullable=True)
    care_unit_guid = Column(String(36), nullable=True)
    actor_user_guid = Column(String(36), nullable=False)
    method = Column(String(16), nullable=False)
    reason = Column(String(500), nullable=True)
    at = Column(DateTime, nullable=False, default=_utcnow)


class PatientInvite(Base):
    __tablename__ = 'patient_invites'

    id = Column(Integer, primary_key=True)
    guid = Column(String(36), unique=True, nullable=False, default=_new_guid)
    token_hash = Column(String(64), unique=True, nullable=False)
    ips_patient_guid = Column(String(36), nullable=False, index=True)
    care_unit_guid = Column(String(36), nullable=False)
    created_by_user_guid = Column(String(36), nullable=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    used_by_user_guid = Column(String(36), nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    revoked_by_user_guid = Column(String(36), nullable=True)


class PatientLinkCode(Base):
    __tablename__ = 'patient_link_codes'

    id = Column(Integer, primary_key=True)
    guid = Column(String(36), unique=True, nullable=False, default=_new_guid)
    user_guid = Column(String(36), ForeignKey('users.guid', ondelete='CASCADE'),
                       nullable=False, index=True)
    code_hash = Column(String(64), unique=True, nullable=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    used_by_user_guid = Column(String(36), nullable=True)
