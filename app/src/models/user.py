"""User model — base identity for all users."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, Boolean, DateTime, Enum
from sqlalchemy.orm import relationship

from src.db import Base


class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True)
    guid = Column(String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    user_type = Column(Enum('patient', 'professional', name='user_type_enum'), nullable=False)
    is_su_admin = Column(Boolean, nullable=False, default=False)
    # Ticket #43: SU-triggered password reset flow.
    # force_change_on_next_login is set to True when an SU resets the user's
    # password; /api/auth/change-password clears it. Login includes the flag
    # in its response so clients can force the user through the change flow.
    # password_changed_at is nullable so pre-existing rows don't need a fabricated
    # value at migration time.
    force_change_on_next_login = Column(Boolean, nullable=False, default=False)
    password_changed_at = Column(DateTime, nullable=True)
    # Ticket #44: bulk session flush. JWT middleware compares the token's iat
    # against this epoch — any token issued strictly before is rejected.
    # Nullable so pre-existing tokens keep working until an SU actively bumps
    # the epoch (= "revoke all sessions for this user right now").
    token_revocation_epoch = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    patient = relationship('Patient', back_populates='user', uselist=False, cascade='all, delete-orphan')
    professional = relationship('Professional', back_populates='user', uselist=False, cascade='all, delete-orphan')
    organisations = relationship('UserOrganisation', back_populates='user', cascade='all, delete-orphan')
    memberships = relationship('Membership', back_populates='user', foreign_keys='Membership.user_guid',
                               primaryjoin='User.guid == Membership.user_guid')

    def __repr__(self):
        return f'<User {self.email} ({self.user_type})>'
