"""#784 — SSO reform: landing page, person registry, organisation hierarchy.

Two of these cover real defects rather than presentation, and both were
invisible to the existing suite:

  * Export CSV answered 401 for every browser click, because the admin page
    linked an `Authorization: Bearer`-only API route. Nothing tested the link,
    only the API route behind it — which worked when called with a token.
  * The Person Registry resolved an organisation's name with `{% set %}` inside
    a nested `{% for %}`. Jinja2 scopes that assignment to the loop iteration,
    so the name was ALWAYS empty on the way out and the cell always rendered a
    truncated GUID. The code meant to show a name could never run, and a test
    that only asserted "the page renders" could never notice.

Both are checked through the real entry point — a browser-shaped request with a
session cookie and no bearer token — because that is the thing that was broken.
"""
import csv
import io

from src.db import get_session
from src.models.organisation import Organisation
from src.models.user import User
from src.models.user_organisation import UserOrganisation
from src.services.auth_service import hash_password, verify_password
from src.services.jwt_service import issue_token

from tests.test_frontend import _login_session, _make_su, SECRET


def _su(client, app):
    guid, token = _make_su(app)
    _login_session(client, app, token)
    return guid


def _org(app, name, parent=None, external=False):
    with app.app_context():
        s = get_session()
        o = Organisation(name=name)
        if parent:
            o.parent_caregiver_guid = parent
        if external:
            o.is_external = True
        s.add(o)
        s.commit()
        return o.guid


# ---------------------------------------------------------------- B1: export

class TestExportCsvActuallyWorks:
    """The reported bug: 'Export CSV does not work'."""

    def test_a_browser_click_downloads_a_csv(self, client, app):
        """No bearer token — only the session cookie a browser sends.

        This is the whole bug. The old link went to /api/admin/export-users,
        whose @require_auth reads ONLY the Authorization header, so this exact
        request returned 401 with a JSON error body in a new tab.
        """
        _su(client, app)
        resp = client.get('/admin/export-users')
        assert resp.status_code == 200, resp.data[:200]
        assert 'text/csv' in resp.headers['Content-Type']
        assert 'attachment' in resp.headers['Content-Disposition']
        rows = list(csv.reader(io.StringIO(resp.data.decode())))
        assert rows, 'no CSV content'
        assert rows[0][0] == 'email'
        assert 'su@test.com' in [r[0] for r in rows[1:]]

    def test_the_api_twin_still_refuses_a_cookie_only_request(self, client, app):
        """The API route is NOT loosened — it keeps requiring a bearer token.

        The fix adds a session-authenticated route beside it; it does not make
        the API half accept cookies. If this ever starts passing, the bearer
        requirement has been weakened and that is a different change.
        """
        _su(client, app)
        resp = client.get('/api/admin/export-users')
        assert resp.status_code == 401

    def test_export_requires_su_like_every_other_admin_route(self, client, app):
        from tests.test_frontend import _make_professional
        guid, token = _make_professional(app)
        _login_session(client, app, token)
        resp = client.get('/admin/export-users', follow_redirects=False)
        assert resp.status_code == 302
        assert '/dashboard' in resp.headers['Location']

    def test_anonymous_cannot_export(self, client, app):
        resp = client.get('/admin/export-users', follow_redirects=False)
        assert resp.status_code == 302
        assert '/login' in resp.headers['Location']


# ------------------------------------------------------- B3: import template

class TestImportTemplate:

    def test_the_template_header_is_what_the_importer_reads(self, client, app):
        """The template is generated from the same contract the importer uses,
        so a round-trip cannot be documented wrongly."""
        _su(client, app)
        resp = client.get('/admin/users-csv-template')
        assert resp.status_code == 200
        assert 'text/csv' in resp.headers['Content-Type']
        rows = list(csv.reader(io.StringIO(resp.data.decode())))
        header, example = rows[0], rows[1]
        assert header[0] == 'email'
        for required in ('email', 'user_type', 'is_su_admin',
                         'professional_role', 'first_name', 'last_name'):
            assert required in header, required
        assert len(example) == len(header)

    def test_a_downloaded_template_can_be_imported_back(self, client, app):
        """End to end: template -> fill -> import. The example row in the
        template is a valid import row, not decoration."""
        _su(client, app)
        tmpl = client.get('/admin/users-csv-template').data
        resp = client.post('/admin/import-users',
                           data={'file': (io.BytesIO(tmpl), 'users.csv')},
                           content_type='multipart/form-data',
                           follow_redirects=True)
        assert resp.status_code == 200
        with app.app_context():
            s = get_session()
            assert s.query(User).filter_by(
                email='anna.exempel@example.org').first() is not None

    def test_an_export_can_be_imported_back(self, client, app):
        """The column order is shared, so the two halves agree."""
        _su(client, app)
        exported = client.get('/admin/export-users').data.decode()
        # Swap the one unique field so the row is new rather than skipped.
        exported = exported.replace('su@test.com', 'copy@test.com')
        resp = client.post('/admin/import-users',
                           data={'file': (io.BytesIO(exported.encode()), 'u.csv')},
                           content_type='multipart/form-data',
                           follow_redirects=True)
        assert resp.status_code == 200
        with app.app_context():
            s = get_session()
            u = s.query(User).filter_by(email='copy@test.com').first()
            assert u is not None
            # The guid column is present in the CSV but must NOT be honoured.
            assert u.guid != 'anything-from-the-csv'


class TestImportPasswordPolicy:

    def test_an_imported_account_must_change_its_password(self, client, app):
        """#784 B3. The Show-template panel states that every imported account
        shares one password. That is only safe to say because the account is
        forced to change it at first login — before this the flag was left
        False and an imported cohort kept a known shared password for good.
        """
        _su(client, app)
        csv_bytes = b'email,user_type\nnewbie@test.com,professional\n'
        client.post('/admin/import-users',
                    data={'file': (io.BytesIO(csv_bytes), 'u.csv')},
                    content_type='multipart/form-data', follow_redirects=True)
        with app.app_context():
            s = get_session()
            u = s.query(User).filter_by(email='newbie@test.com').first()
            assert u is not None
            assert u.force_change_on_next_login is True
            # The documented password is the real one...
            assert verify_password('changeme01', u.password_hash)

    def test_the_gate_actually_stops_an_imported_user(self, client, app):
        """Setting the flag is worth nothing unless something enforces it.
        Go through the gate rather than asserting the column."""
        _su(client, app)
        client.post('/admin/import-users',
                    data={'file': (io.BytesIO(
                        b'email,user_type\ngated@test.com,professional\n'),
                        'u.csv')},
                    content_type='multipart/form-data', follow_redirects=True)
        with app.app_context():
            s = get_session()
            u = s.query(User).filter_by(email='gated@test.com').first()
            token = issue_token(u.guid, SECRET, expiry_hours=1)
        fresh = app.test_client()
        _login_session(fresh, app, token)
        resp = fresh.get('/dashboard', follow_redirects=False)
        assert resp.status_code == 302
        assert '/change-password' in resp.headers['Location']


# ------------------------------------------------- B5: organisation BY NAME

class TestOrganisationShownByName:

    def test_the_person_registry_names_the_organisation(self, client, app):
        """The reported bug. Before the fix this cell always rendered the
        first 8 characters of the GUID, because the name was resolved with
        `{% set %}` inside a nested `{% for %}` and Jinja2 scopes that to the
        loop iteration.
        """
        su_guid = _su(client, app)
        org_guid = _org(app, 'Akademiska Sjukhuset')
        with app.app_context():
            s = get_session()
            s.add(UserOrganisation(user_guid=su_guid, organisation_guid=org_guid))
            s.commit()

        html = client.get('/admin').data.decode()
        assert 'Akademiska Sjukhuset' in html
        # The truncated guid must no longer be what stands in for the name.
        # It may still appear as a tooltip, so look for the chip text itself.
        # Anchor on the rendered attribute. `html.index('org-chip')` would
        # find the CSS rule of the same name first, and a slice of the
        # stylesheet trivially contains neither the name nor the guid --
        # an assertion that passes for the wrong reason.
        chip = html[html.index('class="org-chip"'):][:400]
        assert 'Akademiska Sjukhuset' in chip
        assert org_guid[:8] + '…' not in chip

    def test_an_unresolvable_org_says_so_instead_of_showing_a_bare_guid(
            self, client, app):
        """A dangling assignment is a real state (an org deleted under a
        user). Naming it beats printing 8 hex characters with no label."""
        su_guid = _su(client, app)
        with app.app_context():
            s = get_session()
            s.add(UserOrganisation(user_guid=su_guid,
                                   organisation_guid='dead-org-guid-0000'))
            s.commit()
        html = client.get('/admin').data.decode()
        assert 'unknown org' in html


# --------------------------------------------------- C1: organisation tree

class TestOrganisationHierarchy:

    def test_a_care_unit_is_nested_under_its_caregiver(self, client, app):
        _su(client, app)
        cg = _org(app, 'Region Uppsala')
        _org(app, 'UAS Medicin', parent=cg)
        _org(app, 'UAS Kirurgi', parent=cg)
        html = client.get('/admin').data.decode()

        cg_pos = html.index('Region Uppsala')
        med = html.index('UAS Medicin')
        kir = html.index('UAS Kirurgi')
        # Both units render AFTER their caregiver, i.e. grouped beneath it.
        assert cg_pos < med and cg_pos < kir
        assert 'org-unit' in html
        assert '2 vårdenheter' in html

    def test_the_sparrgrans_meaning_is_stated(self, client, app):
        """The operator's point: a vårdenhet is the spärrgräns. That is a
        legal boundary, so the panel should say it rather than assume it."""
        _su(client, app)
        html = client.get('/admin').data.decode()
        assert 'spärrgräns' in html

    def test_a_caregiver_with_no_units_is_not_silently_blank(self, client, app):
        _su(client, app)
        _org(app, 'Lonely Vårdgivare')
        html = client.get('/admin').data.decode()
        assert 'No vårdenheter yet' in html

    def test_an_external_partner_is_outside_the_hierarchy(self, client, app):
        """Externals get their own section, below the care hierarchy.

        The first version of this test asserted `'External partners' in html`
        and PASSED against the unfixed template -- the old page contained the
        sentence "External partners are managed in the External Partners panel
        below". It matched prose, not structure, so it proved nothing. Assert
        the section row and its spärr note instead, and that the external org
        renders after the caregivers rather than among them.
        """
        _su(client, app)
        cg = _org(app, 'Region Uppsala')
        _org(app, 'UAS Medicin', parent=cg)
        _org(app, 'MeditunerAB', external=True)
        html = client.get('/admin').data.decode()

        # Anchor on the rendered attribute, not the bare class name: the
        # stylesheet defines `.org-section-row` earlier in the document, so
        # html.index('org-section-row') finds the CSS and every ordering
        # assertion built on it compares against the wrong offset.
        section = 'class="org-section-row"'
        assert section in html
        assert 'no spärrgräns' in html
        # Ordering: the care hierarchy first, the external section after it.
        assert html.index('UAS Medicin') < html.index(section)
        assert html.index(section) < html.rindex('MeditunerAB')
        # And it is NOT given a vårdenhet badge.
        ext = html[html.rindex('MeditunerAB') - 300:html.rindex('MeditunerAB')]
        assert 'org-badge-unit' not in ext

    def test_a_unit_whose_caregiver_is_missing_is_surfaced_not_hidden(
            self, client, app):
        """Grouping by parent can make a row with a dangling parent vanish.
        That would be the worst outcome: an organisation that exists, holds
        patients, and cannot be seen or fixed in the panel.
        """
        _su(client, app)
        with app.app_context():
            s = get_session()
            o = Organisation(name='Orphan Unit')
            o.parent_caregiver_guid = 'no-such-caregiver'
            s.add(o)
            s.commit()
        html = client.get('/admin').data.decode()
        assert 'Orphan Unit' in html
        assert 'missing vårdgivare' in html


# ---------------------------------------------------------- D1/D2: removals

class TestRemovedPanes:

    def test_the_documentation_pane_is_gone(self, client, app):
        _su(client, app)
        html = client.get('/admin').data.decode()
        assert '/docs/download/api-reference.md' not in html

    def test_the_docs_themselves_still_work(self, client, app):
        """Only the admin download table was removed. Removing the pane must
        not have taken the documentation routes with it."""
        _su(client, app)
        resp = client.get('/docs/download/api-reference.md')
        assert resp.status_code in (200, 304), resp.status_code

    def test_the_service_registry_pane_is_gone(self, client, app):
        _su(client, app)
        html = client.get('/admin').data.decode()
        assert 'Service Registry' not in html

    def test_the_health_poller_went_with_it(self, client, app):
        """A 30-minute timer repainting dots that no longer exist would be
        invisible and permanent. Removing the table is not enough."""
        _su(client, app)
        html = client.get('/admin').data.decode()
        assert '/api/service-health' not in html
        assert '_refreshHealth' not in html


# ------------------------------------------------------------- B2/B6/E1: text

class TestExplanations:

    def test_the_file_input_says_what_file(self, client, app):
        _su(client, app)
        html = client.get('/admin').data.decode()
        assert 'CSV file of people to add' in html

    def test_every_person_action_button_has_a_tooltip(self, client, app):
        """'Flush' and 'Del' are irreversible and were unlabelled."""
        from tests.test_frontend import _make_professional
        _su(client, app)
        _make_professional(app, email='target@test.com')
        html = client.get('/admin').data.decode()
        for phrase in ('Log this person out of every device',
                       'Delete this person',
                       'temporary password',
                       'super-admin'):
            assert phrase in html, phrase

    def test_the_registries_pane_explains_what_it_decides(self, client, app):
        _su(client, app)
        html = client.get('/admin').data.decode()
        assert 'vocabulary the rest of the platform' in html
        # It must say that research consent is per project, which is the part
        # that surprises people.
        assert 'per project' in html

    def test_the_registries_pane_does_not_claim_the_zone_sets_purpose(
            self, client, app):
        """The consent gate matches on the role NAME, not the zone. An earlier
        draft of this text said 'zone', which would have sent an SU looking in
        the wrong field to change what a role may read.
        """
        _su(client, app)
        html = client.get('/admin').data.decode()
        assert "role's <strong>name</strong>" in html
