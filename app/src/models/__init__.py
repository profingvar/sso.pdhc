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
# Access-model reform (rollup #396): Role registry (S2), ResearchProject
# registry (S4), Affiliation binding (S3).
from src.models.role import Role
from src.models.research_project import ResearchProject
from src.models.affiliation import Affiliation
# Main rule 2026-11-13: a person is employed by one or more vårdgivare.
from src.models.employment import Employment
# 2026-11-19: time-limited external read grants on CDR data.
from src.models.read_grant import ReadGrant
# #1040: temporary lockout of password login after repeated failures.
from src.models.login_lockout import LoginLockout
# #1040: second factor for patient accounts, and the account <-> patient link.
from src.models.second_factor import WebAuthnCredential, TotpSecret
from src.models.patient_link import (
    PatientLink, PatientLinkHistory, PatientInvite, PatientLinkCode,
)
# #1049: the name a self-registered patient gave.
from src.models.patient_profile import PatientProfile
# #1050: the invited patient's name and the wrong-personnummer counter.
from src.models.patient_invite_detail import PatientInviteDetail

__all__ = [
    'ReadGrant',
    'WebAuthnCredential', 'TotpSecret', 'LoginLockout',
    'PatientLink', 'PatientLinkHistory', 'PatientInvite', 'PatientLinkCode',
    'PatientProfile', 'PatientInviteDetail',
    'User', 'Patient', 'Professional', 'Organisation', 'OrganisationAudit',
    'UserOrganisation', 'Group', 'Membership', 'GroupProposal',
    'LeaderRequest', 'AccessRequest', 'Invite', 'RevokedToken', 'UserPhase',
    'Role', 'ResearchProject', 'Affiliation', 'Employment',
]
