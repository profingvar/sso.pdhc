"""Role registry — the professional roles in the PDHC access model.

Reform S2 (ticket #398). Each role names its permitted phases (decision
Item 3, 2026-07-03, permissive: Doctor + Nurse also hold Analysis for local
QA). Phase names are the canonical ones from user_phase.PHASE_NAMES:
  planning (= Plan) · request · provider (= Receive) · analysis

The Plan phase is orthogonal (no patient data) and is NOT listed on any role;
it is granted independently and passes the S5 runtime intersection regardless
of role (see care/ORTHOGONAL_PHASES in the phase-resolution helper).

The role LIST is SU-edit-only (S8, #410); this table is the source of truth
that replaces the legacy 3-value Professional.professional_role enum.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, DateTime, Enum
from sqlalchemy.types import JSON

from src.db import Base
from src.models.user_phase import PHASE_NAMES


def _utcnow():
    return datetime.now(timezone.utc)


def _new_guid():
    return str(uuid.uuid4())


# Seed roles: (code, display_name, zone, permitted_phases).
# zone is informational (care vs analysis). permitted_phases are the phases
# the role may operate in — used by S5 to intersect the person's granted
# phases at session time.
SEED_ROLES = (
    ('doctor',                    'Doctor',                    'care',
     ['request', 'provider', 'analysis']),
    ('nurse',                     'Nurse',                     'care',
     ['request', 'provider', 'analysis']),
    ('other_care',                'Other care professional',   'care',
     ['request', 'provider']),
    ('researcher',                'Researcher',                'analysis',
     ['analysis']),
    ('quality_registry_reporter', 'Quality registry reporter', 'analysis',
     ['analysis']),
    ('local_quality_assured',     'Local quality-assured',     'analysis',
     ['analysis']),
    ('other_analysis',            'Other analysis phase',      'analysis',
     ['analysis']),
)

# Legacy Professional.professional_role -> new Role.code (S3 backfill).
LEGACY_ROLE_MAP = {
    'doctor': 'doctor',
    'nurse': 'nurse',
    'other': 'other_care',
}


class Role(Base):
    __tablename__ = 'roles'

    id = Column(Integer, primary_key=True)
    guid = Column(String(36), unique=True, nullable=False, default=_new_guid)
    code = Column(String(64), unique=True, nullable=False)
    display_name = Column(String(128), nullable=False)
    zone = Column(Enum('care', 'analysis', name='role_zone_enum'),
                  nullable=False, default='care')
    # JSON array of phase names (subset of PHASE_NAMES). Validated on write.
    permitted_phases = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=True, onupdate=_utcnow)

    def validate_permitted_phases(self):
        bad = [p for p in (self.permitted_phases or []) if p not in PHASE_NAMES]
        if bad:
            raise ValueError(f'unknown phase(s) in permitted_phases: {bad}')

    def to_dict(self):
        return {
            'guid': self.guid,
            'code': self.code,
            'display_name': self.display_name,
            'zone': self.zone,
            'permitted_phases': self.permitted_phases or [],
        }

    def __repr__(self):
        return f'<Role {self.code} phases={self.permitted_phases}>'


def seed_roles(session):
    """Idempotently create the seed roles. Returns {code: Role}."""
    out = {}
    for code, display, zone, phases in SEED_ROLES:
        role = session.query(Role).filter_by(code=code).first()
        if role is None:
            role = Role(code=code, display_name=display, zone=zone,
                        permitted_phases=list(phases))
            role.validate_permitted_phases()
            session.add(role)
        out[code] = role
    session.flush()
    return out
