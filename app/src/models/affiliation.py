"""Affiliation — the person↔CareUnit↔Role binding.

Reform S3 (ticket #399). The heart of the access-model reform. Replaces the
flat UserOrganisation M2M: a person holds zero or more affiliations, each
binding them to ONE CareUnit in ONE Role. The same person can be a Doctor at
one clinic and a Researcher at another.

This is what the access blob carries as affiliations[] (S6). During migration
UserOrganisation is kept and backfilled from (below); it is removed only in the
final consumer-migration cleanup (M0, #409).

Backfill role: seeded from the person's legacy Professional.professional_role
via role.LEGACY_ROLE_MAP (decision 2026-07-03 — no placeholder wave).
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, String, DateTime, Boolean, Enum, ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.types import JSON

from src.db import Base


def _utcnow():
    return datetime.now(timezone.utc)


def _new_guid():
    return str(uuid.uuid4())


class Affiliation(Base):
    __tablename__ = 'affiliations'

    id = Column(Integer, primary_key=True)
    guid = Column(String(36), unique=True, nullable=False, default=_new_guid)
    # person_guid == the SSO user guid (the stable Person identity).
    person_guid = Column(String(36), ForeignKey('users.guid'), nullable=False,
                         index=True)
    # care_unit_guid points at an organisations row that is a CareUnit
    # (its parent_caregiver_guid resolves the CareOrganisation — S1).
    care_unit_guid = Column(String(36), ForeignKey('organisations.guid'),
                            nullable=False, index=True)
    role_guid = Column(String(36), ForeignKey('roles.guid'), nullable=False)
    # Only meaningful when the role is 'researcher'. JSON array of
    # ResearchProject guids (S4). Intersected with the patient's consent (D1).
    research_project_guids = Column(JSON, nullable=True)
    is_admin = Column(Boolean, nullable=False, default=False)
    status = Column(Enum('active', 'suspended', name='affiliation_status_enum'),
                    nullable=False, default='active')
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=True, onupdate=_utcnow)

    __table_args__ = (
        # A person holds at most one affiliation per (unit, role) pairing.
        UniqueConstraint('person_guid', 'care_unit_guid', 'role_guid',
                         name='uq_affiliation_person_unit_role'),
    )

    role = relationship('Role')
    care_unit = relationship('Organisation')

    def __repr__(self):
        return (f'<Affiliation person={self.person_guid} '
                f'unit={self.care_unit_guid} role={self.role_guid} '
                f'status={self.status}>')
