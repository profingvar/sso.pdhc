"""ResearchProject registry (ResDB).

Reform S4 (ticket #400). New registry, home = sso.pdhc (decided 2026-07-03,
authorization-adjacent). Referenced by:
  - Affiliation.research_project_guids (S3) — which projects a researcher works on;
  - ips PatientDB.consented_research_projects (D1) — which projects a patient
    consented to.
The analysis-phase research read is the intersection of the two (v3 spec §5.3).

SU-edit-only (S8, #410). May split into a dedicated research-registry service
later if scope grows.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, DateTime, Text

from src.db import Base


def _utcnow():
    return datetime.now(timezone.utc)


def _new_guid():
    return str(uuid.uuid4())


class ResearchProject(Base):
    __tablename__ = 'research_projects'

    id = Column(Integer, primary_key=True)
    guid = Column(String(36), unique=True, nullable=False, default=_new_guid)
    name = Column(String(255), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    # Optional ethics-approval reference (Etikprövningsmyndigheten dnr).
    ethics_ref = Column(String(128), nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=True, onupdate=_utcnow)

    def to_dict(self):
        return {
            'guid': self.guid,
            'name': self.name,
            'description': self.description,
            'ethics_ref': self.ethics_ref,
        }

    def __repr__(self):
        return f'<ResearchProject {self.name}>'
