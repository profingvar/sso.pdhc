"""M0 #409 — the legacy blob-field kill-switch (old #196 final act).

SSO_EMIT_LEGACY_BLOB_FIELDS=false stops dual-emitting the six pre-reform
fields; default (unset/true) keeps today's blob byte-identical.
"""
import pytest

from src.db import get_session
from src.models.role import seed_roles
from src.models.affiliation import Affiliation
from src.models.user_phase import UserPhase
from src.services.auth_service import LEGACY_BLOB_FIELDS, build_access_blob


@pytest.fixture
def pro_with_affiliation(seeded_app, app):
    """The seeded professional, upgraded with an affiliation + phase so the
    reform fields are non-trivial."""
    with app.app_context():
        s = get_session()
        roles = seed_roles(s)
        s.add(Affiliation(person_guid=seeded_app['pro_guid'],
                          care_unit_guid=seeded_app['org_guid'],
                          role_guid=roles['nurse'].guid, status='active'))
        s.add(UserPhase(user_guid=seeded_app['pro_guid'], phase='analysis'))
        s.commit()
        s.close()
    return seeded_app


def _blob(app, data):
    from src.models.user import User
    with app.app_context():
        s = get_session()
        user = s.query(User).filter_by(guid=data['pro_guid']).one()
        blob = build_access_blob(user, s, session_id='sid-test')
        s.close()
        return blob


def test_default_keeps_legacy_fields(app, pro_with_affiliation, monkeypatch):
    monkeypatch.delenv('SSO_EMIT_LEGACY_BLOB_FIELDS', raising=False)
    blob = _blob(app, pro_with_affiliation)
    for key in LEGACY_BLOB_FIELDS:
        assert key in blob, key
    assert blob['affiliations'] and blob['session_phases'] == ['analysis']


def test_flag_off_strips_legacy_keeps_reform(app, pro_with_affiliation,
                                             monkeypatch):
    monkeypatch.setenv('SSO_EMIT_LEGACY_BLOB_FIELDS', 'false')
    blob = _blob(app, pro_with_affiliation)
    for key in LEGACY_BLOB_FIELDS:
        assert key not in blob, key
    # the reform surface is intact
    assert blob['affiliations'][0]['role'] == 'nurse'
    assert blob['session_phases'] == ['analysis']
    assert blob['active_affiliation_guid']
    assert blob['activation_pending'] is False
    assert blob['session_id'] == 'sid-test'


def test_flag_off_pending_branch_also_stripped(app, seeded_app, monkeypatch):
    from src.models.user import User
    monkeypatch.setenv('SSO_EMIT_LEGACY_BLOB_FIELDS', 'false')
    with app.app_context():
        s = get_session()
        user = s.query(User).filter_by(guid=seeded_app['pro_guid']).one()
        user.status = 'pending'
        s.commit()
        blob = build_access_blob(user, s, session_id=None)
        user.status = 'active'
        s.commit()
        s.close()
    assert blob['activation_pending'] is True
    for key in LEGACY_BLOB_FIELDS:
        assert key not in blob, key
    assert blob['session_phases'] == []


def test_patient_blob_untouched(app, seeded_app, monkeypatch):
    from src.models.user import User
    monkeypatch.setenv('SSO_EMIT_LEGACY_BLOB_FIELDS', 'false')
    with app.app_context():
        s = get_session()
        user = s.query(User).filter_by(guid=seeded_app['pat_guid']).one()
        blob = build_access_blob(user, s)
        s.close()
    assert blob['user_type'] == 'patient'
    assert blob['patient_guid']
