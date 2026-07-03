"""Registry management authz tests — reform S8 (#410).

Blob-truth-style contract: the four reform registries (Role, ResearchProject,
CareOrganisation, CareUnit) are SU-edit-only. Non-SU writes -> 403; reads are
open to any authenticated professional. Plus the care-hierarchy 2-level guard
wired into org create/edit.
"""
import uuid


def _hdr(token):
    return {'Authorization': f'Bearer {token}'}


class TestRoleRegistryAuthz:
    def test_read_open_to_professional(self, client, seeded_app):
        r = client.get('/api/registry/roles', headers=_hdr(seeded_app['pro_token']))
        assert r.status_code == 200
        assert isinstance(r.get_json(), list)

    def test_read_requires_auth(self, client, seeded_app):
        assert client.get('/api/registry/roles').status_code == 401

    def test_nonsu_create_forbidden(self, client, seeded_app):
        r = client.post('/api/registry/roles',
                        headers=_hdr(seeded_app['pro_token']),
                        json={'code': 'x', 'display_name': 'X'})
        assert r.status_code == 403

    def test_nonsu_update_forbidden(self, client, seeded_app):
        r = client.put('/api/registry/roles/%s' % uuid.uuid4(),
                       headers=_hdr(seeded_app['pro_token']),
                       json={'display_name': 'X'})
        assert r.status_code == 403

    def test_nonsu_delete_forbidden(self, client, seeded_app):
        r = client.delete('/api/registry/roles/%s' % uuid.uuid4(),
                          headers=_hdr(seeded_app['pro_token']))
        assert r.status_code == 403

    def test_su_create_and_validate_phases(self, client, seeded_app):
        # bad phase rejected
        bad = client.post('/api/registry/roles',
                          headers=_hdr(seeded_app['su_token']),
                          json={'code': 'r1', 'display_name': 'R1',
                                'zone': 'analysis',
                                'permitted_phases': ['not_a_phase']})
        assert bad.status_code == 400
        # good one created
        ok = client.post('/api/registry/roles',
                         headers=_hdr(seeded_app['su_token']),
                         json={'code': 'r1', 'display_name': 'R1',
                               'zone': 'analysis',
                               'permitted_phases': ['analysis']})
        assert ok.status_code == 201
        assert ok.get_json()['code'] == 'r1'
        # duplicate code -> 409
        dup = client.post('/api/registry/roles',
                          headers=_hdr(seeded_app['su_token']),
                          json={'code': 'r1', 'display_name': 'again',
                                'permitted_phases': []})
        assert dup.status_code == 409


class TestResearchProjectRegistryAuthz:
    def test_read_open_to_professional(self, client, seeded_app):
        r = client.get('/api/registry/research-projects',
                       headers=_hdr(seeded_app['pro_token']))
        assert r.status_code == 200

    def test_nonsu_create_forbidden(self, client, seeded_app):
        r = client.post('/api/registry/research-projects',
                        headers=_hdr(seeded_app['pro_token']),
                        json={'name': 'Study A'})
        assert r.status_code == 403

    def test_nonsu_delete_forbidden(self, client, seeded_app):
        r = client.delete('/api/registry/research-projects/%s' % uuid.uuid4(),
                          headers=_hdr(seeded_app['pro_token']))
        assert r.status_code == 403

    def test_su_create_edit_delete(self, client, seeded_app):
        su = _hdr(seeded_app['su_token'])
        c = client.post('/api/registry/research-projects', headers=su,
                        json={'name': 'FEV1 cohort', 'ethics_ref': 'dnr-2026-1'})
        assert c.status_code == 201
        guid = c.get_json()['guid']
        e = client.put(f'/api/registry/research-projects/{guid}', headers=su,
                       json={'description': 'updated'})
        assert e.status_code == 200
        assert e.get_json()['description'] == 'updated'
        d = client.delete(f'/api/registry/research-projects/{guid}', headers=su)
        assert d.status_code == 200


class TestCareRegistryReadProjections:
    def test_care_organisations_read_open(self, client, seeded_app):
        r = client.get('/api/registry/care-organisations',
                       headers=_hdr(seeded_app['pro_token']))
        assert r.status_code == 200
        # the seeded 'Test Hospital' has no parent -> it's a care organisation
        names = [o['care_organisation_name'] for o in r.get_json()]
        assert 'Test Hospital' in names

    def test_care_units_read_open(self, client, seeded_app):
        r = client.get('/api/registry/care-units',
                       headers=_hdr(seeded_app['pro_token']))
        assert r.status_code == 200


class TestOrgCreateHierarchyGuard:
    """S8 wires validate_care_hierarchy into the SU-only org create/edit."""

    def test_create_care_unit_under_org_ok(self, client, seeded_app):
        su = _hdr(seeded_app['su_token'])
        r = client.post('/api/admin/organisations', headers=su,
                        json={'name': 'Unit A',
                              'parent_caregiver_guid': seeded_app['org_guid']})
        assert r.status_code == 201

    def test_create_rejects_three_level(self, client, seeded_app):
        su = _hdr(seeded_app['su_token'])
        # level-2 unit under the seeded org
        u = client.post('/api/admin/organisations', headers=su,
                        json={'name': 'Unit B',
                              'parent_caregiver_guid': seeded_app['org_guid']})
        assert u.status_code == 201
        unit_guid = u.get_json()['organisation_guid']
        # level-3 under the unit must be refused (2-level rule)
        bad = client.post('/api/admin/organisations', headers=su,
                          json={'name': 'Sub-unit C',
                                'parent_caregiver_guid': unit_guid})
        assert bad.status_code == 400
        assert bad.get_json()['error'] == 'invalid_hierarchy'

    def test_create_rejects_unknown_parent(self, client, seeded_app):
        su = _hdr(seeded_app['su_token'])
        bad = client.post('/api/admin/organisations', headers=su,
                          json={'name': 'Orphan',
                                'parent_caregiver_guid': str(uuid.uuid4())})
        assert bad.status_code == 400
