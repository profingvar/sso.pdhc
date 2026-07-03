"""Registry management API — reform S8 (#410).

The reform's selectable reference lists ("dropdowns") are SU-edit-only:

  - Role                (RoleDB,  #398)
  - ResearchProject     (ResDB,   #400)
  - CareOrganisation    (OrgDB,   #397)  — a projection of Organisation
  - CareUnit            (UnitDB,  #397)  — a projection of Organisation

Authorization contract (enforced at the API layer, not just the UI):
  * READ  of every list: any authenticated professional (to populate the
    dropdowns in consumer UIs).  -> @require_auth
  * WRITE (create/edit/delete): SU only. Non-SU (incl. org admins) get 403.
    -> @require_auth + @require_su

CareOrganisation / CareUnit are two views of the existing self-referential
Organisation table (parent_caregiver_guid decides which). Their WRITE path
is the existing /api/admin/organisations CRUD (now guarded by
validate_care_hierarchy, S8) — this blueprint exposes only their READ
projections here so consumer dropdowns have one obvious endpoint each.

Phases are a fixed code-level enum (user_phase.PHASE_NAMES) — deliberately
NOT a registry; there is no admin surface for them (per #410).
"""
from flask import Blueprint, request, jsonify, g

from src.db import get_db
from src.services.audit_log import audit
from src.middleware.auth_middleware import require_auth, require_su

registry_bp = Blueprint('registry', __name__, url_prefix='/api/registry')


# ---------------------------------------------------------------------------
# Role registry (#398)
# ---------------------------------------------------------------------------

@registry_bp.route('/roles', methods=['GET'])
@require_auth
def list_roles():
    """GET /api/registry/roles — read open to any authenticated professional."""
    from src.models.role import Role
    session = get_db()
    roles = session.query(Role).order_by(Role.code).all()
    return jsonify([r.to_dict() for r in roles]), 200


@registry_bp.route('/roles', methods=['POST'])
@require_auth
@require_su
def create_role():
    """POST /api/registry/roles — SU only."""
    from src.models.role import Role
    session = get_db()
    data = request.get_json() if request.is_json else request.form.to_dict()

    code = (data.get('code') or '').strip()
    display_name = (data.get('display_name') or '').strip()
    zone = (data.get('zone') or 'care').strip()
    permitted_phases = data.get('permitted_phases') or []

    if not code or not display_name:
        return jsonify({"error": "invalid_request",
                        "message": "code and display_name required"}), 400
    if zone not in ('care', 'analysis'):
        return jsonify({"error": "invalid_request",
                        "message": "zone must be 'care' or 'analysis'"}), 400
    if not isinstance(permitted_phases, list):
        return jsonify({"error": "invalid_request",
                        "message": "permitted_phases must be a list"}), 400

    if session.query(Role).filter_by(code=code).first():
        return jsonify({"error": "conflict",
                        "message": "role code already exists"}), 409

    role = Role(code=code, display_name=display_name, zone=zone,
                permitted_phases=list(permitted_phases))
    try:
        role.validate_permitted_phases()
    except ValueError as e:
        return jsonify({"error": "invalid_request", "message": str(e)}), 400

    session.add(role)
    session.flush()
    audit('registry.role.create', user_guid=g.current_user.guid,
          detail={'role_guid': role.guid, 'code': code}, ip=request.remote_addr)
    return jsonify(role.to_dict()), 201


@registry_bp.route('/roles/<guid>', methods=['PUT'])
@require_auth
@require_su
def update_role(guid):
    """PUT /api/registry/roles/<guid> — SU only."""
    from src.models.role import Role
    session = get_db()
    role = session.query(Role).filter_by(guid=guid).first()
    if role is None:
        return jsonify({"error": "not_found"}), 404

    data = request.get_json() if request.is_json else request.form.to_dict()

    if 'display_name' in data:
        dn = (data['display_name'] or '').strip()
        if dn:
            role.display_name = dn
    if 'zone' in data:
        if data['zone'] not in ('care', 'analysis'):
            return jsonify({"error": "invalid_request",
                            "message": "zone must be 'care' or 'analysis'"}), 400
        role.zone = data['zone']
    if 'permitted_phases' in data:
        if not isinstance(data['permitted_phases'], list):
            return jsonify({"error": "invalid_request",
                            "message": "permitted_phases must be a list"}), 400
        role.permitted_phases = list(data['permitted_phases'])
        try:
            role.validate_permitted_phases()
        except ValueError as e:
            return jsonify({"error": "invalid_request", "message": str(e)}), 400

    session.flush()
    audit('registry.role.update', user_guid=g.current_user.guid,
          detail={'role_guid': role.guid, 'fields': list(data.keys())},
          ip=request.remote_addr)
    return jsonify(role.to_dict()), 200


@registry_bp.route('/roles/<guid>', methods=['DELETE'])
@require_auth
@require_su
def delete_role(guid):
    """DELETE /api/registry/roles/<guid> — SU only. Refuses if any affiliation
    still references the role (would dangle role_guid)."""
    from src.models.role import Role
    from src.models.affiliation import Affiliation
    session = get_db()
    role = session.query(Role).filter_by(guid=guid).first()
    if role is None:
        return jsonify({"error": "not_found"}), 404

    in_use = session.query(Affiliation).filter_by(role_guid=guid).count()
    if in_use:
        return jsonify({"error": "conflict",
                        "message": f"role in use by {in_use} affiliation(s)"}), 409

    session.delete(role)
    session.flush()
    audit('registry.role.delete', user_guid=g.current_user.guid,
          detail={'role_guid': guid}, ip=request.remote_addr)
    return jsonify({"deleted": guid}), 200


# ---------------------------------------------------------------------------
# ResearchProject registry (#400)
# ---------------------------------------------------------------------------

@registry_bp.route('/research-projects', methods=['GET'])
@require_auth
def list_research_projects():
    """GET /api/registry/research-projects — read open to any professional."""
    from src.models.research_project import ResearchProject
    session = get_db()
    projs = session.query(ResearchProject).order_by(ResearchProject.name).all()
    return jsonify([p.to_dict() for p in projs]), 200


@registry_bp.route('/research-projects', methods=['POST'])
@require_auth
@require_su
def create_research_project():
    """POST /api/registry/research-projects — SU only."""
    from src.models.research_project import ResearchProject
    session = get_db()
    data = request.get_json() if request.is_json else request.form.to_dict()

    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({"error": "invalid_request", "message": "name required"}), 400
    if session.query(ResearchProject).filter_by(name=name).first():
        return jsonify({"error": "conflict",
                        "message": "project name already exists"}), 409

    proj = ResearchProject(
        name=name,
        description=(data.get('description') or '').strip() or None,
        ethics_ref=(data.get('ethics_ref') or '').strip() or None,
    )
    session.add(proj)
    session.flush()
    audit('registry.research_project.create', user_guid=g.current_user.guid,
          detail={'project_guid': proj.guid, 'name': name}, ip=request.remote_addr)
    return jsonify(proj.to_dict()), 201


@registry_bp.route('/research-projects/<guid>', methods=['PUT'])
@require_auth
@require_su
def update_research_project(guid):
    """PUT /api/registry/research-projects/<guid> — SU only."""
    from src.models.research_project import ResearchProject
    session = get_db()
    proj = session.query(ResearchProject).filter_by(guid=guid).first()
    if proj is None:
        return jsonify({"error": "not_found"}), 404

    data = request.get_json() if request.is_json else request.form.to_dict()
    if 'name' in data:
        new_name = (data['name'] or '').strip()
        if new_name and new_name != proj.name:
            if session.query(ResearchProject).filter_by(name=new_name).first():
                return jsonify({"error": "conflict",
                                "message": "name already exists"}), 409
            proj.name = new_name
    if 'description' in data:
        proj.description = (data['description'] or '').strip() or None
    if 'ethics_ref' in data:
        proj.ethics_ref = (data['ethics_ref'] or '').strip() or None

    session.flush()
    audit('registry.research_project.update', user_guid=g.current_user.guid,
          detail={'project_guid': proj.guid, 'fields': list(data.keys())},
          ip=request.remote_addr)
    return jsonify(proj.to_dict()), 200


@registry_bp.route('/research-projects/<guid>', methods=['DELETE'])
@require_auth
@require_su
def delete_research_project(guid):
    """DELETE /api/registry/research-projects/<guid> — SU only."""
    from src.models.research_project import ResearchProject
    session = get_db()
    proj = session.query(ResearchProject).filter_by(guid=guid).first()
    if proj is None:
        return jsonify({"error": "not_found"}), 404
    session.delete(proj)
    session.flush()
    audit('registry.research_project.delete', user_guid=g.current_user.guid,
          detail={'project_guid': guid}, ip=request.remote_addr)
    return jsonify({"deleted": guid}), 200


# ---------------------------------------------------------------------------
# CareOrganisation / CareUnit — READ projections of Organisation (#397)
# (writes go through /api/admin/organisations, now hierarchy-validated)
# ---------------------------------------------------------------------------

@registry_bp.route('/care-organisations', methods=['GET'])
@require_auth
def list_care_organisations():
    """GET /api/registry/care-organisations — dropdown of vårdgivare."""
    from src.services import care_hierarchy
    session = get_db()
    orgs = care_hierarchy.list_care_organisations(session)
    return jsonify([o.care_organisation_dict() for o in orgs]), 200


@registry_bp.route('/care-units', methods=['GET'])
@require_auth
def list_care_units():
    """GET /api/registry/care-units — dropdown of vårdenheter.

    Optional ?care_organisation_guid=<guid> filters to one vårdgivare."""
    from src.services import care_hierarchy
    session = get_db()
    org_guid = request.args.get('care_organisation_guid')
    if org_guid:
        units = care_hierarchy.care_units_for_organisation(session, org_guid)
    else:
        units = care_hierarchy.list_care_units(session)
    return jsonify([u.care_unit_dict() for u in units]), 200
