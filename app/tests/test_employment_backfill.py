"""backfill_employments: the employments existing data implies (2026-11-13)."""
import pytest

from src.db import get_session
from src.models.affiliation import Affiliation
from src.models.employment import Employment
from src.models.organisation import Organisation
from src.models.role import seed_roles
from src.models.user import User
from src.models.user_organisation import UserOrganisation
from src.services.auth_service import hash_password
from src.services.employment_service import backfill_employments


@pytest.fixture
def world(seeded_app, app):
    with app.app_context():
        s = get_session()
        doctor = seed_roles(s)['doctor'].guid
        a = Organisation(name='Caregiver A'); b = Organisation(name='Caregiver B')
        c = Organisation(name='Caregiver C')
        ext = Organisation(name='External X', is_external=True)
        s.add_all([a, b, c, ext]); s.flush()
        a1 = Organisation(name='Unit A1', parent_caregiver_guid=a.guid)
        c1 = Organisation(name='Unit C1', parent_caregiver_guid=c.guid)
        s.add_all([a1, c1]); s.flush()

        def pro(email):
            u = User(email=email, password_hash=hash_password('pass12345678'),
                     user_type='professional', is_su_admin=False)
            s.add(u); s.flush(); return u.guid
        p1, p2, p3 = pro('p1@t.se'), pro('p2@t.se'), pro('p3@t.se')
        pat = User(email='pat@t.se', password_hash=hash_password('pass12345678'),
                   user_type='patient', is_su_admin=False)
        s.add(pat); s.flush()
        # p1: affiliated to A1 (active) and directly to B (suspended)
        s.add(Affiliation(person_guid=p1, care_unit_guid=a1.guid, role_guid=doctor, status='active'))
        s.add(Affiliation(person_guid=p1, care_unit_guid=b.guid, role_guid=doctor, status='suspended'))
        # p2: membership-only, of vårdgivare A, of unit C1, of an external org
        for o in (a.guid, c1.guid, ext.guid):
            s.add(UserOrganisation(user_guid=p2, organisation_guid=o))
        # p3: already employed by A, affiliated to A1
        s.add(Employment(person_guid=p3, caregiver_guid=a.guid))
        s.add(Affiliation(person_guid=p3, care_unit_guid=a1.guid, role_guid=doctor, status='active'))
        # a patient's membership is never an employment
        s.add(UserOrganisation(user_guid=pat.guid, organisation_guid=a.guid))
        w = dict(a=a.guid, b=b.guid, c=c.guid, c1=c1.guid, ext=ext.guid,
                 p1=p1, p2=p2, p3=p3, pat=pat.guid)
        s.commit(); s.close()
        return w


MINE = ('p1', 'p2', 'p3', 'pat')


def _employments(app, w):
    """This test's people only: seeded_app has professionals of its own
    (members of "Test Hospital"), whom the backfill rightly employs too."""
    mine = {w[k] for k in MINE}
    with app.app_context():
        s = get_session()
        out = sorted((e.person_guid, e.caregiver_guid) for e in s.query(Employment)
                     if e.person_guid in mine)
        s.close(); return out


def _mine(rows, w):
    mine = {w[k] for k in MINE}
    return [r for r in rows if r['person_guid'] in mine]


def _run(app, dry_run):
    with app.app_context():
        s = get_session()
        try:
            return backfill_employments(s, dry_run=dry_run)
        finally:
            s.close()


def test_dry_run_reports_and_writes_NOTHING(app, world):
    before = _employments(app, world)
    r = _run(app, True)
    assert len(_mine(r['created'], world)) == 3
    assert _employments(app, world) == before


def test_apply_creates_exactly_the_implied_employments(app, world):
    w = world
    r = _run(app, False)
    got = {(c['person_guid'], c['caregiver_guid'], c['from']) for c in _mine(r['created'], w)}
    assert got == {(w['p1'], w['a'], 'affiliation'),     # via unit A1
                   (w['p1'], w['b'], 'affiliation'),     # suspended still counts
                   (w['p2'], w['a'], 'membership')}      # vårdgivare membership
    assert r['skipped_existing'] == 1                    # p3 already employed by A
    assert _employments(app, w) == sorted(
        [(w['p1'], w['a']), (w['p1'], w['b']), (w['p2'], w['a']), (w['p3'], w['a'])])


def test_a_UNIT_membership_does_not_employ_and_is_reported(app, world):
    r = _run(app, False)
    assert r['unit_memberships_not_used'] == [{'person_guid': world['p2'], 'org_guid': world['c1']}]
    assert (world['p2'], world['c']) not in _employments(app, world)


def test_an_external_membership_is_flagged_not_employed(app, world):
    r = _run(app, False)
    assert [f['org_guid'] for f in r['flagged']] == [world['ext']]
    assert all(cg != world['ext'] for _, cg in _employments(app, world))


def test_a_patient_is_never_employed(app, world):
    _run(app, False)
    assert all(p != world['pat'] for p, _ in _employments(app, world))


def test_a_second_apply_creates_nothing(app, world):
    first = _run(app, False)
    r = _run(app, False)
    assert r['created'] == []
    assert r['skipped_existing'] == first['skipped_existing'] + len(first['created'])
