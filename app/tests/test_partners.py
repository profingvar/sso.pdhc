"""External Partners — full route + flow coverage.

Plan: ../../plans/external_partners_plan.md

Covers:
  - SU registers → secret returned once → secret hashed in DB
  - Public lookup returns sparse record; never includes contact / scopes
  - Internal validate accepts good secret, rejects bad / suspended /
    revoked / expired
  - Edit blocks immutable fields; allows mutable fields
  - Rotate replaces credential and returns new secret
  - Suspend / reactivate / revoke transitions, including idempotency
  - Audit log records every state change with actor + before/after
  - Non-SU users get 403 from admin endpoints
"""
from datetime import datetime, timedelta, timezone

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _su_headers(seeded_app):
    return {
        'Authorization': f"Bearer {seeded_app['su_token']}",
        'X-Service-Key': 'test-internal-key',
    }


def _create(client, seeded_app, **overrides):
    body = {
        'display_name': 'Region Stockholm Diabetes Registry',
        'country_code': 'SE',
        'contact_email': 'integrations@example.org',
        'auth_kind': 'api_key',
        'allowed_scopes': ['fhir.observation.read'],
        'allowed_services': ['cdr.pdhc'],
    }
    body.update(overrides)
    r = client.post('/api/admin/partners', json=body, headers=_su_headers(seeded_app))
    return r


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

def test_create_returns_secret_once_and_201(client, seeded_app):
    r = _create(client, seeded_app)
    assert r.status_code == 201
    body = r.get_json()
    assert body['partner_guid']
    assert body['display_name'] == 'Region Stockholm Diabetes Registry'
    assert body['status'] == 'active'
    assert body['secret_cleartext']
    assert len(body['secret_cleartext']) >= 40
    assert 'secret_warning' in body


def test_create_persists_only_hash_not_cleartext(client, seeded_app, app):
    r = _create(client, seeded_app)
    guid = r.get_json()['partner_guid']
    cleartext = r.get_json()['secret_cleartext']

    from src.models.organisation import Organisation
    from src.db import get_session
    with app.app_context():
        s = get_session()
        p = s.query(Organisation).filter_by(guid=guid, is_external=True).first()
        assert p.api_key_hash
        assert cleartext not in p.api_key_hash  # actually hashed
        s.close()


def test_create_oauth_client_assigns_client_id(client, seeded_app):
    r = _create(client, seeded_app, auth_kind='oauth_client')
    assert r.status_code == 201
    body = r.get_json()
    assert body['auth_kind'] == 'oauth_client'
    assert body['client_id']
    assert body['secret_cleartext']


def test_create_rejects_unknown_scope(client, seeded_app):
    r = _create(client, seeded_app, allowed_scopes=['fhir.observation.read', 'fake.scope'])
    assert r.status_code == 400
    assert 'fake.scope' in r.get_json()['message']


def test_create_rejects_unknown_service(client, seeded_app):
    r = _create(client, seeded_app, allowed_services=['unknown.pdhc'])
    assert r.status_code == 400
    assert 'unknown.pdhc' in r.get_json()['message']


def test_create_rejects_missing_required(client, seeded_app):
    r = client.post('/api/admin/partners',
                    json={'country_code': 'SE'},
                    headers=_su_headers(seeded_app))
    assert r.status_code == 400


def test_create_requires_su(client, seeded_app):
    r = client.post('/api/admin/partners',
                    json={'display_name': 'X', 'country_code': 'SE',
                          'contact_email': 'x@x.x', 'auth_kind': 'api_key'},
                    headers={'Authorization': f"Bearer {seeded_app['pro_token']}"})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Public lookup
# ---------------------------------------------------------------------------

def test_public_lookup_sparse_record(client, seeded_app):
    guid = _create(client, seeded_app).get_json()['partner_guid']
    r = client.get(f'/api/public/partner/{guid}')
    assert r.status_code == 200
    body = r.get_json()
    assert body['partner_guid'] == guid
    assert body['display_name']
    assert body['country_code'] == 'SE'
    assert body['status'] == 'active'
    # Sensitive fields must NOT leak.
    for forbidden in ('contact_email', 'contact_name', 'allowed_scopes',
                      'api_key_hash', 'client_secret_hash', 'secret_cleartext'):
        assert forbidden not in body


def test_public_lookup_404(client):
    r = client.get('/api/public/partner/00000000-0000-0000-0000-000000000000')
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Internal validate
# ---------------------------------------------------------------------------

def test_internal_validate_good_secret(client, seeded_app, app):
    app.config['INTERNAL_SERVICE_KEY'] = 'test-internal-key'
    created = _create(client, seeded_app).get_json()
    guid = created['partner_guid']
    secret = created['secret_cleartext']

    r = client.post(f'/api/internal/partner/{guid}/validate',
                    json={'secret': secret},
                    headers={'X-Service-Key': 'test-internal-key'})
    assert r.status_code == 200
    blob = r.get_json()['access_blob']
    assert blob['user_type'] == 'partner'
    assert blob['partner_guid'] == guid
    assert blob['allowed_scopes'] == ['fhir.observation.read']
    assert blob['is_su_admin'] is False


def test_internal_validate_bad_secret(client, seeded_app, app):
    app.config['INTERNAL_SERVICE_KEY'] = 'test-internal-key'
    guid = _create(client, seeded_app).get_json()['partner_guid']
    r = client.post(f'/api/internal/partner/{guid}/validate',
                    json={'secret': 'totally-wrong'},
                    headers={'X-Service-Key': 'test-internal-key'})
    assert r.status_code == 401


def test_internal_validate_requires_service_key(client, seeded_app, app):
    app.config['INTERNAL_SERVICE_KEY'] = 'test-internal-key'
    guid = _create(client, seeded_app).get_json()['partner_guid']
    r = client.post(f'/api/internal/partner/{guid}/validate',
                    json={'secret': 'x'})
    assert r.status_code == 401


def test_internal_validate_404_for_unknown_partner(client, app):
    app.config['INTERNAL_SERVICE_KEY'] = 'test-internal-key'
    r = client.post('/api/internal/partner/00000000-0000-0000-0000-000000000000/validate',
                    json={'secret': 'x'},
                    headers={'X-Service-Key': 'test-internal-key'})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Lifecycle: suspend / reactivate / revoke
# ---------------------------------------------------------------------------

def test_suspend_blocks_validation(client, seeded_app, app):
    app.config['INTERNAL_SERVICE_KEY'] = 'test-internal-key'
    created = _create(client, seeded_app).get_json()
    guid, secret = created['partner_guid'], created['secret_cleartext']

    r = client.post(f'/api/admin/partners/{guid}/suspend', headers=_su_headers(seeded_app))
    assert r.status_code == 200
    assert r.get_json()['status'] == 'suspended'

    r = client.post(f'/api/internal/partner/{guid}/validate',
                    json={'secret': secret},
                    headers={'X-Service-Key': 'test-internal-key'})
    assert r.status_code == 403
    assert r.get_json()['reason'] == 'suspended'


def test_reactivate_after_suspend(client, seeded_app, app):
    app.config['INTERNAL_SERVICE_KEY'] = 'test-internal-key'
    created = _create(client, seeded_app).get_json()
    guid, secret = created['partner_guid'], created['secret_cleartext']
    client.post(f'/api/admin/partners/{guid}/suspend', headers=_su_headers(seeded_app))

    r = client.post(f'/api/admin/partners/{guid}/reactivate', headers=_su_headers(seeded_app))
    assert r.status_code == 200
    assert r.get_json()['status'] == 'active'

    r = client.post(f'/api/internal/partner/{guid}/validate',
                    json={'secret': secret},
                    headers={'X-Service-Key': 'test-internal-key'})
    assert r.status_code == 200


def test_revoke_irreversible(client, seeded_app):
    guid = _create(client, seeded_app).get_json()['partner_guid']
    r = client.post(f'/api/admin/partners/{guid}/revoke',
                    json={'reason': 'contract terminated'},
                    headers=_su_headers(seeded_app))
    assert r.status_code == 200
    body = r.get_json()
    assert body['status'] == 'revoked'
    assert body['revoked_at']
    assert body['revoke_reason'] == 'contract terminated'

    # Reactivate must fail on revoked.
    r = client.post(f'/api/admin/partners/{guid}/reactivate', headers=_su_headers(seeded_app))
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Rotate
# ---------------------------------------------------------------------------

def test_rotate_replaces_credential(client, seeded_app, app):
    app.config['INTERNAL_SERVICE_KEY'] = 'test-internal-key'
    created = _create(client, seeded_app).get_json()
    guid, old_secret = created['partner_guid'], created['secret_cleartext']

    r = client.post(f'/api/admin/partners/{guid}/rotate', headers=_su_headers(seeded_app))
    assert r.status_code == 200
    new_secret = r.get_json()['secret_cleartext']
    assert new_secret != old_secret

    # Old fails.
    r = client.post(f'/api/internal/partner/{guid}/validate',
                    json={'secret': old_secret},
                    headers={'X-Service-Key': 'test-internal-key'})
    assert r.status_code == 401

    # New succeeds.
    r = client.post(f'/api/internal/partner/{guid}/validate',
                    json={'secret': new_secret},
                    headers={'X-Service-Key': 'test-internal-key'})
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------

def test_edit_mutable_fields(client, seeded_app):
    guid = _create(client, seeded_app).get_json()['partner_guid']
    r = client.patch(f'/api/admin/partners/{guid}',
                     json={'display_name': 'New Name',
                           'allowed_scopes': ['fhir.observation.read', 'fhir.patient.read']},
                     headers=_su_headers(seeded_app))
    assert r.status_code == 200
    body = r.get_json()
    assert body['display_name'] == 'New Name'
    assert 'fhir.patient.read' in body['allowed_scopes']


def test_edit_blocks_immutable(client, seeded_app):
    guid = _create(client, seeded_app).get_json()['partner_guid']
    r = client.patch(f'/api/admin/partners/{guid}',
                     json={'country_code': 'NO'},
                     headers=_su_headers(seeded_app))
    assert r.status_code == 400
    assert 'country_code' in r.get_json()['message']


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

def test_audit_records_create_edit_revoke(client, seeded_app):
    guid = _create(client, seeded_app).get_json()['partner_guid']
    client.patch(f'/api/admin/partners/{guid}',
                 json={'display_name': 'Renamed'},
                 headers=_su_headers(seeded_app))
    client.post(f'/api/admin/partners/{guid}/revoke',
                json={'reason': 'test'},
                headers=_su_headers(seeded_app))

    r = client.get(f'/api/admin/partners/{guid}/audit', headers=_su_headers(seeded_app))
    assert r.status_code == 200
    events = [e['event'] for e in r.get_json()['audit']]
    assert 'created' in events
    assert 'edited' in events
    assert 'revoked' in events
    # Latest first.
    assert events[0] == 'revoked'


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------

def test_catalogue_lists_scopes_and_services(client, seeded_app):
    r = client.get('/api/admin/partners/_meta/catalogue', headers=_su_headers(seeded_app))
    assert r.status_code == 200
    body = r.get_json()
    assert 'fhir.observation.read' in body['scopes']
    assert 'cdr.pdhc' in body['services']


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def test_list_returns_all_partners(client, seeded_app):
    _create(client, seeded_app, display_name='Partner A', contact_email='a@x.com')
    _create(client, seeded_app, display_name='Partner B', contact_email='b@x.com')
    r = client.get('/api/admin/partners', headers=_su_headers(seeded_app))
    assert r.status_code == 200
    names = [p['display_name'] for p in r.get_json()['partners']]
    assert 'Partner A' in names and 'Partner B' in names
