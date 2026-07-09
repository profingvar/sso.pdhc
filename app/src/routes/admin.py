"""SU Admin API routes — user management, groups, proposals, organisations, CSV."""
import csv
import io
import os
import secrets
import string
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify, g, Response

from src.db import get_db
from src.services.auth_service import hash_password, verify_password
from src.services.audit_log import audit
from src.middleware.auth_middleware import require_auth, require_su


def _generate_temp_password(length=16):
    """Generate a high-entropy temporary password.

    Uses letters + digits only (avoids shell-hostile punctuation so the
    SU can safely paste the password into any channel). 16 chars of
    62-symbol alphabet ≈ 95 bits of entropy — plenty for a short-lived
    one-time credential.
    """
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

admin_bp = Blueprint('admin', __name__, url_prefix='/api/admin')


# --- 7.a: List users ---

@admin_bp.route('/users', methods=['GET'])
@require_auth
@require_su
def list_users():
    """GET /api/admin/users — list all users with membership detail."""
    session = get_db()

    from src.models.user import User
    from src.models.professional import Professional
    from src.models.patient import Patient
    from src.models.membership import Membership
    from src.models.group import Group
    from src.models.user_organisation import UserOrganisation

    users = session.query(User).all()
    result = []
    for u in users:
        entry = {
            'user_guid': u.guid,
            'email': u.email,
            'user_type': u.user_type,
            'is_su_admin': u.is_su_admin,
            'created_at': u.created_at.isoformat() if u.created_at else None,
        }

        if u.user_type == 'professional':
            prof = session.query(Professional).filter_by(user_id=u.id).first()
            if prof:
                entry['professional_guid'] = prof.guid
                entry['professional_role'] = prof.professional_role
                entry['first_name'] = prof.first_name
                entry['last_name'] = prof.last_name

            # Memberships
            memberships = session.query(Membership).filter_by(user_guid=u.guid).all()
            entry['memberships'] = []
            for m in memberships:
                grp = session.query(Group).filter_by(guid=m.group_guid).first()
                entry['memberships'].append({
                    'group_guid': m.group_guid,
                    'group_name': grp.name if grp else None,
                    'status': m.status,
                    'is_admin': m.is_admin,
                })

            # Organisations
            user_orgs = session.query(UserOrganisation).filter_by(user_guid=u.guid).all()
            entry['organization_ids'] = [uo.organisation_guid for uo in user_orgs]

        elif u.user_type == 'patient':
            pat = session.query(Patient).filter_by(user_id=u.id).first()
            if pat:
                entry['patient_guid'] = pat.guid
                entry['organisation_guid'] = pat.organisation_guid

        result.append(entry)

    return jsonify(result), 200


# --- 7.b: Promote SU ---

@admin_bp.route('/promote-su', methods=['POST'])
@require_auth
@require_su
def promote_su():
    """POST /api/admin/promote-su — promote user to SU admin. Requires caller password."""
    session = get_db()
    caller = g.current_user

    data = request.get_json() if request.is_json else request.form.to_dict()
    target_guid = data.get('user_guid', '').strip()
    caller_password = data.get('password', '')

    if not target_guid or not caller_password:
        return jsonify({"error": "invalid_request",
                        "message": "user_guid and password required"}), 400

    if not verify_password(caller_password, caller.password_hash):
        return jsonify({"error": "authentication_failed",
                        "message": "Password confirmation failed"}), 401

    from src.models.user import User
    target = session.query(User).filter_by(guid=target_guid).first()
    if target is None:
        return jsonify({"error": "not_found", "message": "User not found"}), 404

    if target.user_type != 'professional':
        return jsonify({"error": "invalid_request",
                        "message": "Only professionals can be SU admins"}), 400

    if target.is_su_admin:
        return jsonify({"error": "conflict", "message": "User is already SU admin"}), 409

    target.is_su_admin = True

    audit('promote_su', user_guid=caller.guid,
          detail={'target_guid': target_guid}, ip=request.remote_addr)

    return jsonify({"user_guid": target_guid, "is_su_admin": True}), 200


# --- 7.c: Delete user ---

@admin_bp.route('/users/<user_guid>', methods=['DELETE'])
@require_auth
@require_su
def delete_user(user_guid):
    """DELETE /api/admin/users/<user_guid> — delete user. Cascade null on decided_by refs."""
    session = get_db()
    caller = g.current_user

    from src.models.user import User
    target = session.query(User).filter_by(guid=user_guid).first()
    if target is None:
        return jsonify({"error": "not_found", "message": "User not found"}), 404

    if target.guid == caller.guid:
        return jsonify({"error": "invalid_request", "message": "Cannot delete yourself"}), 400

    # Null out decided_by references
    from src.models.membership import Membership
    from src.models.group_proposal import GroupProposal
    from src.models.leader_request import LeaderRequest
    from src.models.access_request import AccessRequest

    session.query(Membership).filter_by(decided_by_guid=user_guid).update(
        {'decided_by_guid': None}, synchronize_session='fetch')
    session.query(GroupProposal).filter_by(decided_by_guid=user_guid).update(
        {'decided_by_guid': None}, synchronize_session='fetch')
    session.query(LeaderRequest).filter_by(decided_by_guid=user_guid).update(
        {'decided_by_guid': None}, synchronize_session='fetch')
    session.query(AccessRequest).filter_by(decided_by_guid=user_guid).update(
        {'decided_by_guid': None}, synchronize_session='fetch')

    # Delete memberships for this user
    session.query(Membership).filter_by(user_guid=user_guid).delete(synchronize_session='fetch')

    # Delete user (cascades to patient/professional via relationship)
    session.delete(target)

    audit('delete_user', user_guid=caller.guid,
          detail={'deleted_guid': user_guid, 'email': target.email},
          ip=request.remote_addr)

    return jsonify({"message": "User deleted", "user_guid": user_guid}), 200


# --- 7.c2: Reset user password (ticket #43) ---

@admin_bp.route('/users/<user_guid>/reset-password', methods=['POST'])
@require_auth
@require_su
def reset_user_password(user_guid):
    """POST /api/admin/users/<user_guid>/reset-password — SU resets a user's password.

    Body (optional): {"temp_password": "..."} — if absent, one is generated.
    Sets force_change_on_next_login=True so the user is pushed through the
    change-password flow on their next login. The plaintext temp password
    is returned in the response ONCE and is never persisted.
    """
    session = get_db()
    caller = g.current_user

    from src.models.user import User
    target = session.query(User).filter_by(guid=user_guid).first()
    if target is None:
        return jsonify({"error": "not_found", "message": "User not found"}), 404

    data = request.get_json(silent=True) or {}
    supplied = (data.get('temp_password') or '').strip()

    if supplied:
        if len(supplied) < 8:
            return jsonify({"error": "invalid_request",
                            "message": "temp_password must be at least 8 characters"}), 400
        temp_password = supplied
    else:
        temp_password = _generate_temp_password()

    target.password_hash = hash_password(temp_password)
    target.force_change_on_next_login = True
    target.password_changed_at = datetime.now(timezone.utc)

    audit('reset_password', user_guid=caller.guid,
          detail={'target_guid': user_guid, 'email': target.email,
                  'generated': not bool(supplied)},
          ip=request.remote_addr)

    return jsonify({
        "user_guid": user_guid,
        "email": target.email,
        "temp_password": temp_password,
        "force_change_on_next_login": True,
    }), 200


# --- 7.c3: Flush all sessions for a user (ticket #44) ---

@admin_bp.route('/users/<user_guid>/flush-sessions', methods=['POST'])
@require_auth
@require_su
def flush_user_sessions(user_guid):
    """POST /api/admin/users/<user_guid>/flush-sessions — bulk revoke all
    outstanding JWTs for this user by bumping their token_revocation_epoch.

    Any JWT with iat < new epoch is rejected by validate_token. Cheaper than
    revoking each token individually — also works retroactively for tokens
    issued by services we don't keep a revocation list for (sibling services
    validate via /me/service and trust our response).
    """
    session = get_db()
    caller = g.current_user

    from src.models.user import User
    target = session.query(User).filter_by(guid=user_guid).first()
    if target is None:
        return jsonify({"error": "not_found", "message": "User not found"}), 404

    new_epoch = datetime.now(timezone.utc)
    target.token_revocation_epoch = new_epoch

    audit('flush_sessions', user_guid=caller.guid,
          detail={'target_guid': user_guid, 'email': target.email,
                  'epoch': new_epoch.isoformat()},
          ip=request.remote_addr)

    return jsonify({
        "user_guid": user_guid,
        "email": target.email,
        "token_revocation_epoch": new_epoch.isoformat(),
    }), 200


# --- 7.c4: User phases (ticket #46) ---

@admin_bp.route('/users/<user_guid>/phases', methods=['GET'])
@require_auth
@require_su
def list_user_phases(user_guid):
    """GET /api/admin/users/<guid>/phases — list phases with source.

    Returns direct UserPhase grants plus informational "via_group" entries
    for groups whose `category` happens to match a phase name. After #57
    those via_group entries are **display-only diagnostic info** — they no
    longer confer phase access; only direct UserPhase grants do. After #60
    the column is `category` (free-form varchar), so matches are only
    meaningful when an admin used a legacy phase-shaped label.
    """
    session = get_db()

    from src.models.user import User
    from src.models.user_phase import UserPhase, PHASE_NAMES
    from src.models.membership import Membership
    from src.models.group import Group

    user = session.query(User).filter_by(guid=user_guid).first()
    if user is None:
        return jsonify({"error": "not_found", "message": "User not found"}), 404

    result = []
    # Direct grants
    for up in session.query(UserPhase).filter_by(user_guid=user_guid).all():
        result.append({
            'phase': up.phase,
            'source': 'direct',
            'granted_by_guid': up.granted_by_guid,
            'granted_at': up.granted_at.isoformat() if up.granted_at else None,
        })
    # Implicit via group membership
    memberships = session.query(Membership).filter_by(
        user_guid=user_guid, status='approved').all()
    for m in memberships:
        group = session.query(Group).filter_by(guid=m.group_guid).first()
        if group and group.category in PHASE_NAMES:
            result.append({
                'phase': group.category,
                'source': f'via_group:{group.name}',
                'group_guid': group.guid,
            })

    return jsonify({
        'user_guid': user_guid,
        'available_phases': list(PHASE_NAMES),
        'phases': result,
    }), 200


@admin_bp.route('/users/<user_guid>/phases', methods=['POST'])
@require_auth
@require_su
def grant_user_phase(user_guid):
    """POST /api/admin/users/<guid>/phases body {phase} — direct grant.

    Idempotent: if the user already has this direct grant, returns 200 with
    the existing row rather than a 409. Implicit grants via group membership
    don't block a direct grant (the SU may want both so removing the user
    from the group doesn't revoke phase access).
    """
    session = get_db()
    caller = g.current_user

    data = request.get_json() if request.is_json else request.form.to_dict()
    phase = (data.get('phase') or '').strip()

    from src.models.user import User
    from src.models.user_phase import UserPhase, PHASE_NAMES

    if phase not in PHASE_NAMES:
        return jsonify({"error": "invalid_request",
                        "message": f"phase must be one of {list(PHASE_NAMES)}"}), 400

    user = session.query(User).filter_by(guid=user_guid).first()
    if user is None:
        return jsonify({"error": "not_found", "message": "User not found"}), 404

    existing = session.query(UserPhase).filter_by(
        user_guid=user_guid, phase=phase).first()
    if existing:
        return jsonify({
            "user_guid": user_guid, "phase": phase, "created": False,
        }), 200

    up = UserPhase(user_guid=user_guid, phase=phase,
                   granted_by_guid=caller.guid)
    session.add(up)

    audit('grant_user_phase', user_guid=caller.guid,
          detail={'target_guid': user_guid, 'phase': phase},
          ip=request.remote_addr)

    return jsonify({
        "user_guid": user_guid, "phase": phase, "created": True,
    }), 201


@admin_bp.route('/users/<user_guid>/phases/<phase>', methods=['DELETE'])
@require_auth
@require_su
def revoke_user_phase(user_guid, phase):
    """DELETE /api/admin/users/<guid>/phases/<phase> — revoke direct grant.

    Only removes the direct UserPhase row. Any implicit grant via approved
    group membership is untouched — the user keeps the phase in their
    effective_phases until removed from the group too. The response flags
    `still_implicit` so the SU can see that happen.
    """
    session = get_db()
    caller = g.current_user

    from src.models.user_phase import UserPhase, PHASE_NAMES
    from src.models.membership import Membership
    from src.models.group import Group

    if phase not in PHASE_NAMES:
        return jsonify({"error": "invalid_request",
                        "message": f"phase must be one of {list(PHASE_NAMES)}"}), 400

    up = session.query(UserPhase).filter_by(
        user_guid=user_guid, phase=phase).first()
    if up is None:
        return jsonify({"error": "not_found",
                        "message": "No direct grant for this phase"}), 404

    session.delete(up)

    # After #57 groups no longer confer phases — this flag is display-only
    # diagnostic info so the admin UI can warn "user is still a member of a
    # group whose category matches this phase name". Free-form `category`
    # after #60 means this is only meaningful for legacy phase-shaped labels.
    still_implicit = False
    memberships = session.query(Membership).filter_by(
        user_guid=user_guid, status='approved').all()
    for m in memberships:
        group = session.query(Group).filter_by(guid=m.group_guid).first()
        if group and group.category == phase:
            still_implicit = True
            break

    audit('revoke_user_phase', user_guid=caller.guid,
          detail={'target_guid': user_guid, 'phase': phase,
                  'still_implicit_via_group': still_implicit},
          ip=request.remote_addr)

    return jsonify({
        "user_guid": user_guid, "phase": phase, "revoked": True,
        "still_implicit_via_group": still_implicit,
    }), 200


# --- 7.d: Delete group ---

@admin_bp.route('/groups/<group_guid>', methods=['DELETE'])
@require_auth
@require_su
def delete_group(group_guid):
    """DELETE /api/admin/groups/<group_guid> — delete group with cascade."""
    session = get_db()
    caller = g.current_user

    from src.models.group import Group
    from src.models.membership import Membership
    from src.models.invite import Invite

    group = session.query(Group).filter_by(guid=group_guid).first()
    if group is None:
        return jsonify({"error": "not_found", "message": "Group not found"}), 404

    # Delete related memberships and invites
    session.query(Membership).filter_by(group_guid=group_guid).delete(synchronize_session='fetch')
    session.query(Invite).filter_by(group_guid=group_guid).delete(synchronize_session='fetch')
    session.delete(group)

    audit('delete_group', user_guid=caller.guid,
          detail={'group_guid': group_guid, 'group_name': group.name},
          ip=request.remote_addr)

    return jsonify({"message": "Group deleted", "group_guid": group_guid}), 200


# --- 7.e: Assign group admin ---

@admin_bp.route('/assign-group-admin', methods=['POST'])
@require_auth
@require_su
def assign_group_admin():
    """POST /api/admin/assign-group-admin — set user as admin in group."""
    session = get_db()
    caller = g.current_user

    data = request.get_json() if request.is_json else request.form.to_dict()
    user_guid = data.get('user_guid', '').strip()
    group_guid = data.get('group_guid', '').strip()

    if not user_guid or not group_guid:
        return jsonify({"error": "invalid_request",
                        "message": "user_guid and group_guid required"}), 400

    from src.models.membership import Membership
    membership = session.query(Membership).filter_by(
        user_guid=user_guid, group_guid=group_guid
    ).first()

    if membership is None:
        return jsonify({"error": "not_found",
                        "message": "User is not a member of this group"}), 404

    membership.is_admin = True

    audit('assign_group_admin', user_guid=caller.guid,
          detail={'target_guid': user_guid, 'group_guid': group_guid},
          ip=request.remote_addr)

    return jsonify({"user_guid": user_guid, "group_guid": group_guid, "is_admin": True}), 200


# --- 7.f: Group proposals ---

@admin_bp.route('/group-proposals', methods=['GET'])
@require_auth
@require_su
def list_group_proposals():
    """GET /api/admin/group-proposals — list pending proposals."""
    session = get_db()
    from src.models.group_proposal import GroupProposal

    proposals = session.query(GroupProposal).filter_by(status='pending').all()
    return jsonify([{
        'proposal_guid': p.guid,
        'proposed_name': p.proposed_name,
        'category': p.category,
        'requested_by_guid': p.requested_by_guid,
        'status': p.status,
        'created_at': p.created_at.isoformat() if p.created_at else None,
    } for p in proposals]), 200


@admin_bp.route('/group-proposals', methods=['POST'])
@require_auth
@require_su
def decide_group_proposal():
    """POST /api/admin/group-proposals — approve or reject. Approve creates the group."""
    session = get_db()
    caller = g.current_user

    data = request.get_json() if request.is_json else request.form.to_dict()
    proposal_guid = data.get('proposal_guid', '').strip()
    decision = data.get('decision', '').strip().lower()

    if not proposal_guid or decision not in ('approved', 'rejected'):
        return jsonify({"error": "invalid_request",
                        "message": "proposal_guid and decision (approved/rejected) required"}), 400

    from src.models.group_proposal import GroupProposal
    proposal = session.query(GroupProposal).filter_by(guid=proposal_guid).first()
    if proposal is None:
        return jsonify({"error": "not_found", "message": "Proposal not found"}), 404

    if proposal.status != 'pending':
        return jsonify({"error": "conflict",
                        "message": f"Already decided ({proposal.status})"}), 409

    proposal.status = decision
    proposal.decided_by_guid = caller.guid

    group_guid = None
    if decision == 'approved':
        from src.models.group import Group
        group = Group(name=proposal.proposed_name, category=proposal.category)
        session.add(group)
        session.flush()
        group_guid = group.guid

    audit('group_proposal_decide', user_guid=caller.guid,
          detail={'proposal_guid': proposal_guid, 'decision': decision,
                  'group_guid': group_guid},
          ip=request.remote_addr)

    return jsonify({
        "proposal_guid": proposal_guid,
        "status": decision,
        "group_guid": group_guid,
    }), 200


# --- 7.g: Leader requests ---

@admin_bp.route('/leader-requests', methods=['GET'])
@require_auth
@require_su
def list_leader_requests():
    """GET /api/admin/leader-requests — list pending leader requests."""
    session = get_db()
    from src.models.leader_request import LeaderRequest

    requests = session.query(LeaderRequest).filter_by(status='pending').all()
    return jsonify([{
        'leader_request_guid': r.guid,
        'user_guid': r.user_guid,
        'group_guid': r.group_guid,
        'status': r.status,
        'created_at': r.created_at.isoformat() if r.created_at else None,
    } for r in requests]), 200


@admin_bp.route('/leader-requests', methods=['POST'])
@require_auth
@require_su
def decide_leader_request():
    """POST /api/admin/leader-requests — approve or reject. Approve sets is_admin on membership."""
    session = get_db()
    caller = g.current_user

    data = request.get_json() if request.is_json else request.form.to_dict()
    leader_request_guid = data.get('leader_request_guid', '').strip()
    decision = data.get('decision', '').strip().lower()

    if not leader_request_guid or decision not in ('approved', 'rejected'):
        return jsonify({"error": "invalid_request",
                        "message": "leader_request_guid and decision required"}), 400

    from src.models.leader_request import LeaderRequest
    lr = session.query(LeaderRequest).filter_by(guid=leader_request_guid).first()
    if lr is None:
        return jsonify({"error": "not_found", "message": "Leader request not found"}), 404

    if lr.status != 'pending':
        return jsonify({"error": "conflict",
                        "message": f"Already decided ({lr.status})"}), 409

    lr.status = decision
    lr.decided_by_guid = caller.guid

    if decision == 'approved':
        from src.models.membership import Membership
        membership = session.query(Membership).filter_by(
            user_guid=lr.user_guid, group_guid=lr.group_guid
        ).first()
        if membership:
            membership.is_admin = True

    audit('leader_request_decide', user_guid=caller.guid,
          detail={'leader_request_guid': leader_request_guid, 'decision': decision},
          ip=request.remote_addr)

    return jsonify({
        "leader_request_guid": leader_request_guid,
        "status": decision,
    }), 200


# --- 7.h: Access requests ---

@admin_bp.route('/access-requests', methods=['GET'])
@require_auth
@require_su
def list_access_requests():
    """GET /api/admin/access-requests — list pending/endorsed access requests."""
    session = get_db()
    from src.models.access_request import AccessRequest

    requests = session.query(AccessRequest).filter(
        AccessRequest.status.in_(['pending', 'endorsed'])
    ).all()
    return jsonify([{
        'access_request_guid': r.guid,
        'email': r.email,
        'first_name': r.first_name,
        'last_name': r.last_name,
        'professional_role': r.professional_role,
        'organisation_guid': r.organisation_guid,
        'requested_phases': r.requested_phases or [],
        'chosen_leader_guid': r.chosen_leader_guid,
        'status': r.status,
        'created_at': r.created_at.isoformat() if r.created_at else None,
    } for r in requests]), 200


@admin_bp.route('/access-requests', methods=['POST'])
@require_auth
@require_su
def decide_access_request():
    """POST /api/admin/access-requests — endorse, approve, or reject.

    Approve creates User + Professional + Organisation link. Phase access
    (#46) and group membership are **independent criteria** after #57 —
    this endpoint no longer auto-grants either. The `requested_phases`
    field on the access request is preserved as advisory metadata so the
    reviewing SU can one-click grant via `POST /admin/users/<guid>/phases`
    after approval. Group memberships, likewise, are granted separately
    via the group-admin flow.
    """
    session = get_db()
    caller = g.current_user

    data = request.get_json() if request.is_json else request.form.to_dict()
    access_request_guid = data.get('access_request_guid', '').strip()
    decision = data.get('decision', '').strip().lower()

    if not access_request_guid or decision not in ('endorsed', 'approved', 'rejected'):
        return jsonify({"error": "invalid_request",
                        "message": "access_request_guid and decision required"}), 400

    from src.models.access_request import AccessRequest
    ar = session.query(AccessRequest).filter_by(guid=access_request_guid).first()
    if ar is None:
        return jsonify({"error": "not_found", "message": "Access request not found"}), 404

    if ar.status in ('approved', 'rejected'):
        return jsonify({"error": "conflict",
                        "message": f"Already decided ({ar.status})"}), 409

    ar.status = decision
    ar.decided_by_guid = caller.guid

    user_guid = None
    if decision == 'approved':
        # Create user + professional
        from src.models.user import User
        from src.models.professional import Professional
        from src.models.user_organisation import UserOrganisation

        user = User(
            email=ar.email,
            password_hash=ar.password_hash,
            user_type='professional',
            is_su_admin=False,
            # S9 (#411): approval creates a PENDING person with zero access.
            # The SU then assigns >=1 affiliation + phases and activates via
            # POST /users/<guid>/activate (completeness-enforced). The
            # request's organisation_guid / professional_role remain the
            # non-authoritative HINT the guided UI prefills from.
            status='pending',
        )
        session.add(user)
        session.flush()

        prof = Professional(
            user_id=user.id,
            professional_role=ar.professional_role,
            first_name=ar.first_name,
            last_name=ar.last_name,
        )
        session.add(prof)

        # Link to organisation
        uo = UserOrganisation(user_guid=user.guid, organisation_guid=ar.organisation_guid)
        session.add(uo)

        session.flush()
        user_guid = user.guid

    audit('access_request_decide', user_guid=caller.guid,
          detail={'access_request_guid': access_request_guid,
                  'decision': decision, 'created_user_guid': user_guid,
                  # #57: recorded for audit, but NOT auto-granted; SU
                  # follows up with explicit phase + group grants.
                  'requested_phases_pending_su_grant':
                      list(ar.requested_phases or [])},
          ip=request.remote_addr)

    return jsonify({
        "access_request_guid": access_request_guid,
        "status": decision,
        "user_guid": user_guid,
        # Surface the advisory phases to the UI so the SU can one-click
        # grant them immediately after approval (#57).
        "requested_phases_pending_su_grant": list(ar.requested_phases or []),
        # S9 (#411): the created user is PENDING until the SU completes the
        # guided assignment (affiliation + phases) and activates. The
        # request's org/role are surfaced as the prefill hint.
        "activation_pending": decision == 'approved',
        "hint": ({"care_unit_guid": ar.organisation_guid,
                  "professional_role": ar.professional_role}
                 if decision == 'approved' else None),
    }), 200


# --- 7.i: Organisations ---

@admin_bp.route('/organisations', methods=['GET'])
@require_auth
@require_su
def list_organisations():
    """GET /api/admin/organisations — list all organisations. FHIR Organization shape."""
    session = get_db()
    from src.models.organisation import Organisation

    orgs = session.query(Organisation).all()
    return jsonify([{
        'resourceType': Organisation.FHIR_RESOURCE_TYPE,
        'organisation_guid': o.guid,
        'name': o.name,
        'push_endpoint_url': o.push_endpoint_url,
        'has_push_secret': bool(o.push_secret),
        'created_at': o.created_at.isoformat() if o.created_at else None,
    } for o in orgs]), 200


@admin_bp.route('/organisations', methods=['POST'])
@require_auth
@require_su
def create_organisation():
    """POST /api/admin/organisations — create new organisation."""
    session = get_db()
    caller = g.current_user

    data = request.get_json() if request.is_json else request.form.to_dict()
    name = data.get('name', '').strip()

    if not name:
        return jsonify({"error": "invalid_request", "message": "name required"}), 400

    from src.models.organisation import Organisation
    existing = session.query(Organisation).filter_by(name=name).first()
    if existing:
        return jsonify({"error": "conflict", "message": "Organisation name already exists"}), 409

    org = Organisation(name=name)
    # S8 (#410): a new org may declare its parent vårdgivare, making it a
    # CareUnit; validate the 2-level care hierarchy before committing.
    parent_guid = (data.get('parent_caregiver_guid') or '').strip() or None
    if parent_guid:
        org.parent_caregiver_guid = parent_guid
    session.add(org)
    session.flush()

    from src.services.care_hierarchy import validate_care_hierarchy, HierarchyError
    try:
        validate_care_hierarchy(session, org)
    except HierarchyError as e:
        session.rollback()
        return jsonify({"error": "invalid_hierarchy", "message": str(e)}), 400

    audit('create_organisation', user_guid=caller.guid,
          detail={'org_guid': org.guid, 'name': name,
                  'parent_caregiver_guid': parent_guid}, ip=request.remote_addr)

    return jsonify({
        'resourceType': Organisation.FHIR_RESOURCE_TYPE,
        'organisation_guid': org.guid,
        'name': org.name,
    }), 201


@admin_bp.route('/organisations/<org_guid>', methods=['PUT'])
@require_auth
@require_su
def update_organisation(org_guid):
    """PUT /api/admin/organisations/<guid> — update organisation fields."""
    session = get_db()
    caller = g.current_user

    from src.models.organisation import Organisation
    org = session.query(Organisation).filter_by(guid=org_guid).first()
    if org is None:
        return jsonify({"error": "not_found"}), 404

    data = request.get_json() if request.is_json else request.form.to_dict()

    if 'name' in data:
        new_name = data['name'].strip()
        if new_name and new_name != org.name:
            existing = session.query(Organisation).filter_by(name=new_name).first()
            if existing:
                return jsonify({"error": "conflict", "message": "Name already exists"}), 409
            org.name = new_name

    if 'push_endpoint_url' in data:
        org.push_endpoint_url = data['push_endpoint_url'].strip() or None

    if 'push_secret' in data:
        org.push_secret = data['push_secret'].strip() or None

    if 'parent_caregiver_guid' in data:
        # S8 (#410): allow re-parenting; validate the 2-level care hierarchy.
        org.parent_caregiver_guid = (data['parent_caregiver_guid'] or '').strip() or None

    session.flush()

    from src.services.care_hierarchy import validate_care_hierarchy, HierarchyError
    try:
        validate_care_hierarchy(session, org)
    except HierarchyError as e:
        session.rollback()
        return jsonify({"error": "invalid_hierarchy", "message": str(e)}), 400

    audit('update_organisation', user_guid=caller.guid,
          detail={'org_guid': org.guid, 'fields': list(data.keys())}, ip=request.remote_addr)

    return jsonify({
        'resourceType': Organisation.FHIR_RESOURCE_TYPE,
        'organisation_guid': org.guid,
        'name': org.name,
        'push_endpoint_url': org.push_endpoint_url,
        'has_push_secret': bool(org.push_secret),
    }), 200


@admin_bp.route('/organisations/<org_guid>/dependents', methods=['GET'])
@require_auth
@require_su
def organisation_dependents(org_guid):
    """GET /api/admin/organisations/<guid>/dependents — ticket #45 UI helper.

    Returns counts so the Delete confirm modal can show what will be
    affected, without the SU having to probe by trying to delete.
    """
    session = get_db()

    from src.models.organisation import Organisation
    from src.models.patient import Patient
    from src.models.user_organisation import UserOrganisation
    from src.models.access_request import AccessRequest

    org = session.query(Organisation).filter_by(guid=org_guid).first()
    if org is None:
        return jsonify({"error": "not_found"}), 404

    return jsonify({
        "organisation_guid": org_guid,
        "name": org.name,
        "patients": session.query(Patient).filter_by(organisation_guid=org_guid).count(),
        "user_assignments": session.query(UserOrganisation).filter_by(
            organisation_guid=org_guid).count(),
        "access_requests": session.query(AccessRequest).filter_by(
            organisation_guid=org_guid).count(),
    }), 200


@admin_bp.route('/organisations/<org_guid>', methods=['DELETE'])
@require_auth
@require_su
def delete_organisation(org_guid):
    """DELETE /api/admin/organisations/<guid> — SU-only.

    Ticket #45 cascade policy:
    - Patient references → 409 (hard block; medical data integrity). The
      response lists dependent count + up to 5 sample patient GUIDs so the
      SU can decide what to do.
    - UserOrganisation → deleted (soft link; professionals simply lose the
      assignment).
    - Pending AccessRequest rows pointing at this org → deleted (org is gone,
      the request can't be fulfilled).
    - Push-config columns live on the Organisation row itself, so they
      vanish with the org.
    """
    session = get_db()
    caller = g.current_user

    from src.models.organisation import Organisation
    from src.models.patient import Patient
    from src.models.user_organisation import UserOrganisation
    from src.models.access_request import AccessRequest

    org = session.query(Organisation).filter_by(guid=org_guid).first()
    if org is None:
        return jsonify({"error": "not_found"}), 404

    # Hard block: patients anchor medical data to this org.
    patient_count = session.query(Patient).filter_by(organisation_guid=org_guid).count()
    if patient_count > 0:
        sample = [p.guid for p in session.query(Patient)
                  .filter_by(organisation_guid=org_guid).limit(5).all()]
        return jsonify({
            "error": "conflict",
            "message": f"Organisation still has {patient_count} patient(s); delete blocked.",
            "patient_count": patient_count,
            "sample_patient_guids": sample,
        }), 409

    # Soft cascades — all FKs to organisations.guid default to RESTRICT in
    # Postgres, so every referring row must go before the Organisation row.
    # UserOrganisation: professional's link evaporates; they'll see a "No
    # org" badge on the SU panel until reassigned.
    uo_deleted = session.query(UserOrganisation).filter_by(
        organisation_guid=org_guid).delete(synchronize_session='fetch')
    # AccessRequest: delete ALL (pending, endorsed, approved, rejected).
    # The audit log (audit_log table, written via audit()) keeps the record
    # of this delete and of prior decisions, so the history isn't lost.
    ar_deleted = session.query(AccessRequest).filter_by(
        organisation_guid=org_guid
    ).delete(synchronize_session='fetch')

    org_name = org.name
    session.delete(org)

    audit('delete_organisation', user_guid=caller.guid,
          detail={'org_guid': org_guid, 'name': org_name,
                  'user_assignments_removed': uo_deleted,
                  'access_requests_removed': ar_deleted},
          ip=request.remote_addr)

    return jsonify({
        "message": "Organisation deleted",
        "organisation_guid": org_guid,
        "user_assignments_removed": uo_deleted,
        "access_requests_removed": ar_deleted,
    }), 200


@admin_bp.route('/organisations/<org_guid>/push-config', methods=['GET'])
@require_auth
@require_su
def get_org_push_config(org_guid):
    """GET /api/admin/organisations/<guid>/push-config — push config for auto-provisioning."""
    session = get_db()
    from src.models.organisation import Organisation

    org = session.query(Organisation).filter_by(guid=org_guid).first()
    if org is None:
        return jsonify({"error": "not_found"}), 404

    return jsonify({
        'organisation_guid': org.guid,
        'name': org.name,
        'push_endpoint_url': org.push_endpoint_url,
        'push_secret': org.push_secret,
    }), 200


# --- 7.i2: User-Organisation assignment ---

@admin_bp.route('/users/<user_guid>/organisations', methods=['GET'])
@require_auth
@require_su
def list_user_organisations(user_guid):
    """GET /api/admin/users/<user_guid>/organisations — list orgs for a user."""
    session = get_db()
    from src.models.user import User
    from src.models.user_organisation import UserOrganisation
    from src.models.organisation import Organisation

    user = session.query(User).filter_by(guid=user_guid).first()
    if user is None:
        return jsonify({"error": "not_found", "message": "User not found"}), 404

    user_orgs = session.query(UserOrganisation).filter_by(user_guid=user_guid).all()
    result = []
    for uo in user_orgs:
        org = session.query(Organisation).filter_by(guid=uo.organisation_guid).first()
        result.append({
            'organisation_guid': uo.organisation_guid,
            'name': org.name if org else None,
            'assigned_at': uo.created_at.isoformat() if uo.created_at else None,
        })

    return jsonify(result), 200


@admin_bp.route('/users/<user_guid>/organisations', methods=['POST'])
@require_auth
@require_su
def assign_user_organisation(user_guid):
    """POST /api/admin/users/<user_guid>/organisations — assign user to org."""
    session = get_db()
    caller = g.current_user

    data = request.get_json() if request.is_json else request.form.to_dict()
    org_guid = data.get('organisation_guid', '').strip()

    if not org_guid:
        return jsonify({"error": "invalid_request",
                        "message": "organisation_guid required"}), 400

    from src.models.user import User
    from src.models.organisation import Organisation
    from src.models.user_organisation import UserOrganisation

    user = session.query(User).filter_by(guid=user_guid).first()
    if user is None:
        return jsonify({"error": "not_found", "message": "User not found"}), 404

    if user.user_type != 'professional':
        return jsonify({"error": "invalid_request",
                        "message": "Only professionals can be assigned to organisations"}), 400

    org = session.query(Organisation).filter_by(guid=org_guid).first()
    if org is None:
        return jsonify({"error": "not_found", "message": "Organisation not found"}), 404

    existing = session.query(UserOrganisation).filter_by(
        user_guid=user_guid, organisation_guid=org_guid
    ).first()
    if existing:
        return jsonify({"error": "conflict",
                        "message": "User already assigned to this organisation"}), 409

    uo = UserOrganisation(user_guid=user_guid, organisation_guid=org_guid)
    session.add(uo)

    audit('assign_user_organisation', user_guid=caller.guid,
          detail={'target_guid': user_guid, 'org_guid': org_guid, 'org_name': org.name},
          ip=request.remote_addr)

    return jsonify({
        "user_guid": user_guid,
        "organisation_guid": org_guid,
        "name": org.name,
    }), 201


@admin_bp.route('/users/<user_guid>/organisations/<org_guid>', methods=['DELETE'])
@require_auth
@require_su
def remove_user_organisation(user_guid, org_guid):
    """DELETE /api/admin/users/<user_guid>/organisations/<org_guid> — remove assignment."""
    session = get_db()
    caller = g.current_user

    from src.models.user_organisation import UserOrganisation

    uo = session.query(UserOrganisation).filter_by(
        user_guid=user_guid, organisation_guid=org_guid
    ).first()
    if uo is None:
        return jsonify({"error": "not_found",
                        "message": "User-organisation assignment not found"}), 404

    session.delete(uo)

    audit('remove_user_organisation', user_guid=caller.guid,
          detail={'target_guid': user_guid, 'org_guid': org_guid},
          ip=request.remote_addr)

    return jsonify({"message": "Organisation assignment removed",
                    "user_guid": user_guid, "organisation_guid": org_guid}), 200


# --- 7.j: Export/Import users ---

@admin_bp.route('/export-users', methods=['GET'])
@require_auth
@require_su
def export_users():
    """GET /api/admin/export-users — CSV export of users."""
    session = get_db()
    from src.models.user import User
    from src.models.professional import Professional
    from src.models.patient import Patient

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['user_guid', 'email', 'user_type', 'is_su_admin',
                     'first_name', 'last_name', 'professional_role', 'created_at'])

    users = session.query(User).all()
    for u in users:
        first_name, last_name, role = '', '', ''
        if u.user_type == 'professional':
            prof = session.query(Professional).filter_by(user_id=u.id).first()
            if prof:
                first_name = prof.first_name or ''
                last_name = prof.last_name or ''
                role = prof.professional_role or ''
        writer.writerow([u.guid, u.email, u.user_type, u.is_su_admin,
                        first_name, last_name, role,
                        u.created_at.isoformat() if u.created_at else ''])

    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=users_export.csv'},
    )


@admin_bp.route('/import-users', methods=['POST'])
@require_auth
@require_su
def import_users():
    """POST /api/admin/import-users — import users from CSV."""
    session = get_db()
    caller = g.current_user

    if 'file' not in request.files:
        return jsonify({"error": "invalid_request", "message": "CSV file required"}), 400

    file = request.files['file']
    content = file.read().decode('utf-8')
    reader = csv.DictReader(io.StringIO(content))

    from src.models.user import User
    from src.models.professional import Professional

    created = 0
    skipped = 0
    errors = []

    for row in reader:
        email = row.get('email', '').strip()
        if not email:
            continue

        if session.query(User).filter_by(email=email).first():
            skipped += 1
            continue

        user_type = row.get('user_type', 'professional')
        user = User(
            email=email,
            password_hash=hash_password('changeme01'),  # Temporary password
            user_type=user_type,
            is_su_admin=row.get('is_su_admin', '').lower() == 'true',
        )
        session.add(user)
        session.flush()

        if user_type == 'professional':
            prof = Professional(
                user_id=user.id,
                professional_role=row.get('professional_role', 'other'),
                first_name=row.get('first_name', ''),
                last_name=row.get('last_name', ''),
            )
            session.add(prof)

        created += 1

    audit('import_users', user_guid=caller.guid,
          detail={'created': created, 'skipped': skipped}, ip=request.remote_addr)

    return jsonify({
        "created": created,
        "skipped": skipped,
        "errors": errors,
    }), 200


# --- 7.k: Oath overview ---

@admin_bp.route('/oath-overview', methods=['GET'])
@require_auth
@require_su
def get_oath_overview():
    """GET /api/admin/oath-overview — read oath_overview.csv."""
    from flask import current_app
    oath_path = os.path.join(current_app.root_path, '..', 'oath_overview.csv')

    if not os.path.exists(oath_path):
        return jsonify([]), 200

    with open(oath_path, 'r') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    return jsonify(rows), 200


@admin_bp.route('/oath-overview', methods=['PUT'])
@require_auth
@require_su
def update_oath_overview():
    """PUT /api/admin/oath-overview — update oath_overview.csv."""
    from flask import current_app
    session = get_db()
    caller = g.current_user

    data = request.get_json()
    if not isinstance(data, list):
        return jsonify({"error": "invalid_request", "message": "Array of rows required"}), 400

    oath_path = os.path.join(current_app.root_path, '..', 'oath_overview.csv')

    if not data:
        return jsonify({"error": "invalid_request", "message": "Empty data"}), 400

    fieldnames = list(data[0].keys())
    with open(oath_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(data)

    audit('oath_overview_update', user_guid=caller.guid,
          detail={'rows': len(data)}, ip=request.remote_addr)

    return jsonify({"message": "Oath overview updated", "rows": len(data)}), 200


# --- S9 (#411): SU-only affiliation assignment + guided activation ---
#
# The person triggers (public access-request); the SU assigns. Only SU
# may create/remove Affiliation rows (non-SU -> 403 via @require_su).
# Activation is blocked server-side while the profile is incomplete —
# the guided UI renders the same `missing` list the API computes.


@admin_bp.route('/users/<user_guid>/affiliations', methods=['GET'])
@require_auth
@require_su
def list_user_affiliations(user_guid):
    """GET /api/admin/users/<guid>/affiliations — with names for the UI."""
    session = get_db()
    from src.models.user import User
    from src.services.affiliation_service import build_affiliations_for_blob
    from src.services.activation_service import completeness

    user = session.query(User).filter_by(guid=user_guid).first()
    if user is None:
        return jsonify({"error": "not_found", "message": "User not found"}), 404
    return jsonify({
        "user_guid": user_guid,
        "affiliations": build_affiliations_for_blob(session, user_guid),
        "completeness": completeness(session, user),
    }), 200


@admin_bp.route('/users/<user_guid>/affiliations', methods=['POST'])
@require_auth
@require_su
def create_user_affiliation(user_guid):
    """POST /api/admin/users/<guid>/affiliations — SU assigns (CareUnit + Role).

    Body: {care_unit_guid, role_guid, research_project_guids?, is_admin?}.
    Validations (server-side, S9):
      - user exists and is a professional
      - care_unit_guid is an existing INTERNAL organisation
      - role_guid exists in the role registry (#398)
      - role=researcher requires >=1 research_project_guid, each present
        in the ResearchProject registry (#400); other roles must not
        carry project guids
      - (person, unit, role) unique -> 409 on duplicate
    """
    session = get_db()
    caller = g.current_user
    from src.models.user import User
    from src.models.organisation import Organisation
    from src.models.role import Role
    from src.models.research_project import ResearchProject
    from src.models.affiliation import Affiliation

    user = session.query(User).filter_by(guid=user_guid).first()
    if user is None:
        return jsonify({"error": "not_found", "message": "User not found"}), 404
    if user.user_type != 'professional':
        return jsonify({"error": "invalid_request",
                        "message": "Affiliations apply to professionals only"}), 400

    data = request.get_json(silent=True) or {}
    care_unit_guid = (data.get('care_unit_guid') or '').strip()
    role_guid = (data.get('role_guid') or '').strip()
    project_guids = list(data.get('research_project_guids') or [])
    is_admin = bool(data.get('is_admin', False))

    if not care_unit_guid or not role_guid:
        return jsonify({"error": "invalid_request",
                        "message": "care_unit_guid and role_guid required"}), 400

    unit = session.query(Organisation).filter_by(guid=care_unit_guid).first()
    if unit is None or unit.is_external:
        return jsonify({"error": "invalid_request",
                        "message": "care_unit_guid must be an internal organisation"}), 400

    role = session.query(Role).filter_by(guid=role_guid).first()
    if role is None:
        return jsonify({"error": "invalid_request",
                        "message": "role_guid not in the role registry"}), 400

    if role.code == 'researcher':
        if not project_guids:
            return jsonify({"error": "invalid_request",
                            "message": "researcher affiliation requires >=1 "
                                       "research_project_guid (ResDB #400)"}), 400
        known = {p.guid for p in session.query(ResearchProject).filter(
            ResearchProject.guid.in_(project_guids)).all()}
        unknown = [p for p in project_guids if p not in known]
        if unknown:
            return jsonify({"error": "invalid_request",
                            "message": f"unknown research projects: {unknown}"}), 400
    elif project_guids:
        return jsonify({"error": "invalid_request",
                        "message": "research_project_guids only valid for "
                                   "role=researcher"}), 400

    dup = session.query(Affiliation).filter_by(
        person_guid=user_guid, care_unit_guid=care_unit_guid,
        role_guid=role_guid).first()
    if dup is not None:
        return jsonify({"error": "conflict",
                        "message": "affiliation already exists",
                        "affiliation_guid": dup.guid}), 409

    aff = Affiliation(
        person_guid=user_guid, care_unit_guid=care_unit_guid,
        role_guid=role_guid,
        research_project_guids=project_guids or None,
        is_admin=is_admin, status='active',
    )
    session.add(aff)
    session.flush()

    audit('affiliation_create', user_guid=caller.guid,
          detail={'person_guid': user_guid, 'affiliation_guid': aff.guid,
                  'care_unit_guid': care_unit_guid, 'role': role.code,
                  'research_project_guids': project_guids},
          ip=request.remote_addr)

    from src.services.activation_service import completeness
    return jsonify({
        "affiliation_guid": aff.guid,
        "completeness": completeness(session, user),
    }), 201


@admin_bp.route('/users/<user_guid>/affiliations/<affiliation_guid>',
                methods=['DELETE'])
@require_auth
@require_su
def delete_user_affiliation(user_guid, affiliation_guid):
    """DELETE — remove one affiliation. If it was the person's last one
    and they are active, they stay active (grandfathered) but the
    completeness endpoint flags the gap; suspension is a separate call."""
    session = get_db()
    caller = g.current_user
    from src.models.affiliation import Affiliation

    aff = session.query(Affiliation).filter_by(
        guid=affiliation_guid, person_guid=user_guid).first()
    if aff is None:
        return jsonify({"error": "not_found",
                        "message": "Affiliation not found"}), 404
    session.delete(aff)
    audit('affiliation_delete', user_guid=caller.guid,
          detail={'person_guid': user_guid,
                  'affiliation_guid': affiliation_guid},
          ip=request.remote_addr)
    return jsonify({"deleted": affiliation_guid}), 200


@admin_bp.route('/users/<user_guid>/completeness', methods=['GET'])
@require_auth
@require_su
def user_completeness(user_guid):
    """GET — the guided-form checklist (server-computed, S9)."""
    session = get_db()
    from src.models.user import User
    from src.services.activation_service import completeness

    user = session.query(User).filter_by(guid=user_guid).first()
    if user is None:
        return jsonify({"error": "not_found", "message": "User not found"}), 404
    return jsonify({"user_guid": user_guid,
                    **completeness(session, user)}), 200


@admin_bp.route('/users/<user_guid>/activate', methods=['POST'])
@require_auth
@require_su
def activate_user(user_guid):
    """POST — activate a pending professional. 409 + the missing list
    while the profile is incomplete (server-enforced; the UI cannot
    bypass this by hiding fields)."""
    session = get_db()
    caller = g.current_user
    from src.models.user import User
    from src.services.activation_service import completeness

    user = session.query(User).filter_by(guid=user_guid).first()
    if user is None:
        return jsonify({"error": "not_found", "message": "User not found"}), 404

    check = completeness(session, user)
    if not check['complete']:
        return jsonify({"error": "incomplete_profile",
                        "message": "activation blocked — profile incomplete",
                        **check}), 409

    user.status = 'active'
    audit('user_activate', user_guid=caller.guid,
          detail={'person_guid': user_guid}, ip=request.remote_addr)
    return jsonify({"user_guid": user_guid, "status": "active",
                    **check}), 200


@admin_bp.route('/users/<user_guid>/deactivate', methods=['POST'])
@require_auth
@require_su
def deactivate_user(user_guid):
    """POST — set a professional back to pending (access revoked on the
    next blob build; pair with flush-sessions for immediate effect)."""
    session = get_db()
    caller = g.current_user
    from src.models.user import User

    user = session.query(User).filter_by(guid=user_guid).first()
    if user is None:
        return jsonify({"error": "not_found", "message": "User not found"}), 404
    if user.is_su_admin:
        return jsonify({"error": "invalid_request",
                        "message": "cannot deactivate an SU admin"}), 400
    user.status = 'pending'
    audit('user_deactivate', user_guid=caller.guid,
          detail={'person_guid': user_guid}, ip=request.remote_addr)
    return jsonify({"user_guid": user_guid, "status": "pending"}), 200
