"""All SQLAlchemy models — import here to register with Base.metadata."""
from src.models.user import User
from src.models.patient import Patient
from src.models.professional import Professional
from src.models.organisation import Organisation, OrganisationAudit
from src.models.user_organisation import UserOrganisation
from src.models.group import Group
from src.models.membership import Membership
from src.models.group_proposal import GroupProposal
from src.models.leader_request import LeaderRequest
from src.models.access_request import AccessRequest
from src.models.invite import Invite
from src.models.revoked_token import RevokedToken
from src.models.user_phase import UserPhase

__all__ = [
    'User', 'Patient', 'Professional', 'Organisation', 'OrganisationAudit',
    'UserOrganisation', 'Group', 'Membership', 'GroupProposal',
    'LeaderRequest', 'AccessRequest', 'Invite', 'RevokedToken', 'UserPhase',
]
