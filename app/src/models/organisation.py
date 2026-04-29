"""Organisation model — FHIR resource type: Organization. Single source of
truth for both internal PDHC organisations (hospitals, regions, clinics)
and external partners (vendors, integrators, third-party data providers).

Per ticket #96, external partners do not get a separate table — splitting
them off would break FHIR Contract.signer.party.reference, which uniformly
points at Organisation. The `is_external` flag distinguishes the two
populations; the partner-specific columns (auth_kind, allowed_scopes, etc.)
are nullable for internal orgs and populated for external ones.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, DateTime, Boolean, Enum, ForeignKey, Text
from sqlalchemy.orm import relationship
from sqlalchemy.types import JSON

from src.db import Base


def _utcnow():
    return datetime.now(timezone.utc)


def _new_guid():
    return str(uuid.uuid4())


class Organisation(Base):
    __tablename__ = 'organisations'

    FHIR_RESOURCE_TYPE = 'Organization'

    id = Column(Integer, primary_key=True)
    guid = Column(String(36), unique=True, nullable=False, default=_new_guid)
    name = Column(String(255), unique=True, nullable=False)
    fhir_resource_type = Column(String(50), nullable=False, default='Organization')
    push_endpoint_url = Column(String(512), nullable=True)
    push_secret = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)

    # ---- External-partner extension (ticket #96 unification) ------------
    # When False (default), this is an internal PDHC org and the columns
    # below are unused. When True, this is a registered third-party caller
    # and the columns describe how it authenticates and what it's licensed
    # to call.
    is_external = Column(Boolean, nullable=False, default=False)

    # Identity / business metadata (mostly relevant for external partners)
    country_code = Column(String(2), nullable=True)         # ISO 3166-1 alpha-2
    org_number = Column(String(32), nullable=True)          # Swedish orgnr or equivalent
    description = Column(Text, nullable=True)
    contact_name = Column(String(120), nullable=True)
    contact_email = Column(String(120), nullable=True)
    contact_phone = Column(String(40), nullable=True)

    # Auth / license (external only)
    auth_kind = Column(
        Enum('oauth_client', 'api_key', 'none',
             name='org_auth_kind_enum'),
        nullable=True,
    )
    client_id = Column(String(64), nullable=True)
    client_secret_hash = Column(String(255), nullable=True)
    api_key_hash = Column(String(255), nullable=True)
    allowed_scopes = Column(JSON, nullable=True)            # array of strings
    allowed_services = Column(JSON, nullable=True)          # array of strings
    allowed_org_guids = Column(JSON, nullable=True)         # nullable = unrestricted

    # Lifecycle (external only — internal orgs use a separate workflow)
    status = Column(
        Enum('active', 'suspended', 'revoked',
             name='org_status_enum'),
        nullable=True,
    )
    expires_at = Column(DateTime(timezone=True), nullable=True)
    last_rotated_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoke_reason = Column(Text, nullable=True)

    # Bookkeeping
    updated_at = Column(DateTime(timezone=True), nullable=True, onupdate=_utcnow)
    created_by_user_guid = Column(String(36), ForeignKey('users.guid'), nullable=True)

    audit_entries = relationship(
        'OrganisationAudit',
        back_populates='organisation',
        cascade='all, delete-orphan',
        order_by='OrganisationAudit.at.desc()',
    )

    def __repr__(self):
        kind = 'external' if self.is_external else 'internal'
        return f'<Organisation {kind}: {self.name}>'

    # ----- Projections ---------------------------------------------------

    def public_dict(self):
        """Sparse projection for the public lookup endpoint — display name,
        country, status, description. No contact info, no scopes, no auth
        material. Plan §6.2."""
        return {
            'guid': self.guid,
            'name': self.name,
            'is_external': self.is_external,
            'country_code': self.country_code,
            'status': self.status,
            'description': self.description,
        }

    def admin_dict(self):
        """Full projection for the SU admin page. Never exposes hashes."""
        return {
            'guid': self.guid,
            'name': self.name,
            'is_external': self.is_external,
            'country_code': self.country_code,
            'org_number': self.org_number,
            'description': self.description,
            'contact_name': self.contact_name,
            'contact_email': self.contact_email,
            'contact_phone': self.contact_phone,
            'auth_kind': self.auth_kind,
            'client_id': self.client_id,
            'allowed_scopes': self.allowed_scopes or [],
            'allowed_services': self.allowed_services or [],
            'allowed_org_guids': self.allowed_org_guids,
            'status': self.status,
            'push_endpoint_url': self.push_endpoint_url,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'last_rotated_at': self.last_rotated_at.isoformat() if self.last_rotated_at else None,
            'revoked_at': self.revoked_at.isoformat() if self.revoked_at else None,
            'revoke_reason': self.revoke_reason,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'created_by_user_guid': self.created_by_user_guid,
        }


class OrganisationAudit(Base):
    """Append-only audit log for organisation state changes (Rule 24).

    Used primarily for external-partner lifecycle events but applies to any
    organisation change — useful for governance and Rule-24 compliance."""
    __tablename__ = 'organisation_audit'

    id = Column(Integer, primary_key=True)
    organisation_guid = Column(String(36), ForeignKey('organisations.guid'), nullable=False, index=True)
    actor_user_guid = Column(String(36), ForeignKey('users.guid'), nullable=True)
    event = Column(
        Enum('created', 'edited', 'rotated', 'suspended', 'reactivated', 'revoked',
             name='org_audit_event_enum'),
        nullable=False,
    )
    at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    before_json = Column(JSON, nullable=True)
    after_json = Column(JSON, nullable=True)

    organisation = relationship('Organisation', back_populates='audit_entries')

    def to_dict(self):
        return {
            'id': self.id,
            'organisation_guid': self.organisation_guid,
            'actor_user_guid': self.actor_user_guid,
            'event': self.event,
            'at': self.at.isoformat() if self.at else None,
            'before': self.before_json,
            'after': self.after_json,
        }
