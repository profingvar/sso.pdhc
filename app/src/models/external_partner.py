"""External Partner model — registered third-party callers of the PDHC platform.

Plan: ../../plans/external_partners_plan.md §3.

A Partner is an external organisation (vendor, integrator, regional health
authority) that has been licensed to call PDHC services. Partners auth via
the existing service-key path with the source ``partner:<partner_guid>`` so
no new auth surface is introduced (plan §4).

Two tables:

  ExternalPartner       — current state of each registered partner
  ExternalPartnerAudit  — append-only log of every state change (Rule 24)

Credentials (client_secret / api_key) are hashed with the same scheme as
user passwords (argon2 via ``hash_password``); the cleartext is shown ONCE
to the SU at creation/rotation, then never stored.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, Enum, ForeignKey, Integer, String, Text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.types import JSON

from src.db import Base


def _utcnow():
    return datetime.now(timezone.utc)


def _new_guid():
    return str(uuid.uuid4())


class ExternalPartner(Base):
    """Registered external organisation — licensed caller of PDHC services."""
    __tablename__ = 'external_partner'

    id = Column(Integer, primary_key=True)
    guid = Column(String(36), unique=True, nullable=False, default=_new_guid)

    # ---- Identity / business metadata ----
    display_name = Column(String(120), nullable=False)
    org_number = Column(String(32), nullable=True)        # Swedish orgnr or equivalent
    country_code = Column(String(2), nullable=False)      # ISO 3166-1 alpha-2
    description = Column(Text, nullable=True)
    contact_name = Column(String(120), nullable=True)
    contact_email = Column(String(120), nullable=False)
    contact_phone = Column(String(40), nullable=True)

    # ---- Auth / license ----
    auth_kind = Column(
        Enum('oauth_client', 'api_key', 'none', name='partner_auth_kind_enum'),
        nullable=False, default='api_key',
    )
    client_id = Column(String(64), nullable=True)
    client_secret_hash = Column(String(255), nullable=True)
    api_key_hash = Column(String(255), nullable=True)

    # JSON arrays of strings — kept loose-typed at the DB layer so SQLite
    # tests pass without JSONB. Validated in the route layer.
    allowed_scopes = Column(JSON, nullable=False, default=list)
    allowed_services = Column(JSON, nullable=False, default=list)
    allowed_org_guids = Column(JSON, nullable=True)  # nullable = unrestricted

    # ---- Lifecycle ----
    status = Column(
        Enum('active', 'suspended', 'revoked', name='partner_status_enum'),
        nullable=False, default='active',
    )
    expires_at = Column(DateTime(timezone=True), nullable=True)
    last_rotated_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoke_reason = Column(Text, nullable=True)

    # ---- Bookkeeping ----
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)
    created_by_user_guid = Column(String(36), ForeignKey('users.guid'), nullable=True)

    audit_entries = relationship(
        'ExternalPartnerAudit',
        back_populates='partner',
        cascade='all, delete-orphan',
        order_by='ExternalPartnerAudit.at.desc()',
    )

    def public_dict(self):
        """Sparse projection for the public lookup endpoint — no contact info,
        no scopes, no auth material. Plan §6.2."""
        return {
            'partner_guid': self.guid,
            'display_name': self.display_name,
            'country_code': self.country_code,
            'status': self.status,
            'description': self.description,
        }

    def admin_dict(self):
        """Full projection for the SU admin page. Never exposes hashes."""
        return {
            'partner_guid': self.guid,
            'display_name': self.display_name,
            'org_number': self.org_number,
            'country_code': self.country_code,
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
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'last_rotated_at': self.last_rotated_at.isoformat() if self.last_rotated_at else None,
            'revoked_at': self.revoked_at.isoformat() if self.revoked_at else None,
            'revoke_reason': self.revoke_reason,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'created_by_user_guid': self.created_by_user_guid,
        }


class ExternalPartnerAudit(Base):
    """Append-only audit log for partner state changes. Plan §3.2."""
    __tablename__ = 'external_partner_audit'

    id = Column(Integer, primary_key=True)
    partner_guid = Column(String(36), ForeignKey('external_partner.guid'), nullable=False, index=True)
    actor_user_guid = Column(String(36), ForeignKey('users.guid'), nullable=True)
    event = Column(
        Enum('created', 'edited', 'rotated', 'suspended', 'reactivated', 'revoked',
             name='partner_audit_event_enum'),
        nullable=False,
    )
    at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    before_json = Column(JSON, nullable=True)
    after_json = Column(JSON, nullable=True)

    partner = relationship('ExternalPartner', back_populates='audit_entries')

    def to_dict(self):
        return {
            'id': self.id,
            'partner_guid': self.partner_guid,
            'actor_user_guid': self.actor_user_guid,
            'event': self.event,
            'at': self.at.isoformat() if self.at else None,
            'before': self.before_json,
            'after': self.after_json,
        }
