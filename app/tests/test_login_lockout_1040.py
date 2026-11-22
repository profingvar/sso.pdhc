"""#1040 — rate limit on the HTML login, and temporary lockout of password
login after repeated wrong passwords.

Through the real routes: the HTML form (POST /login) and the JSON door
(POST /api/auth/login). The clock is injected (`login_lockout._now`); nothing
sleeps. `audit` is a recorder, so nothing reaches the audit FILE.
"""
import re
from datetime import datetime, timedelta, timezone

import pytest

from src.db import get_session
from src.middleware.rate_limit import reset_rate_limits
from src.models.login_lockout import LoginLockout
from src.services import login_lockout

GOOD = 'propass1234'
T0 = datetime(2026, 11, 22, 12, 0, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now

    def forward(self, **kw):
        self.now += timedelta(**kw)


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(login_lockout, '_now', c)
    return c


@pytest.fixture
def events(monkeypatch):
    seen = []

    def record(event, user_guid=None, detail=None, ip=None):
        seen.append({'event': event, 'user_guid': user_guid, 'detail': detail or {}})

    for mod in ('src.services.audit_log', 'src.routes.frontend',
                'src.routes.auth', 'src.routes.admin'):
        monkeypatch.setattr(mod + '.audit', record)
    return seen


def _html(client, email, pw):
    """POST the HTML form. Returns (status, body with the per-request CSRF
    noise absent — CSRF is off in tests)."""
    r = client.post('/login', data={'email': email, 'password': pw})
    return r


def _api(client, email, pw):
    return client.post('/api/auth/login', json={'email': email, 'password': pw})


def _let_in_html(r):
    return r.status_code == 302


def _fail(client, email, n, door=_api):
    for _ in range(n):
        reset_rate_limits()            # this file tests the LOCK; the limit has its own test
        door(client, email, 'wrong-password')
    reset_rate_limits()


def test_five_failures_lock_then_time_unlocks(client, seeded_app, clock, events):
    email = seeded_app['pro_email']
    _fail(client, email, 4)
    assert _api(client, email, GOOD).status_code == 200          # 4 is not yet a lock
    _fail(client, email, 5)
    # Locked: the RIGHT password is refused, at both doors.
    assert _api(client, email, GOOD).status_code == 401
    assert not _let_in_html(_html(client, email, GOOD))
    clock.forward(minutes=14, seconds=59)
    assert _api(client, email, GOOD).status_code == 401
    clock.forward(seconds=2)
    assert _api(client, email, GOOD).status_code == 200          # it ended by itself
    locked = [e for e in events if e['event'] == 'login_locked']
    assert len(locked) == 1 and locked[0]['user_guid'] == seeded_app['pro_guid']
    assert locked[0]['detail']['locked_until'] == (T0 + timedelta(minutes=15)).isoformat()
    assert any(e['event'] == 'login_refused_locked' for e in events)


def test_the_two_doors_share_one_counter(client, seeded_app, clock, events):
    email = seeded_app['pro_email']
    _fail(client, email, 3, door=_html)
    _fail(client, email, 2, door=_api)
    assert _api(client, email, GOOD).status_code == 401
    assert not _let_in_html(_html(client, email, GOOD))


def test_success_resets_the_counter(client, seeded_app, clock, events):
    email = seeded_app['pro_email']
    _fail(client, email, 4)
    assert _api(client, email, GOOD).status_code == 200
    _fail(client, email, 4)
    assert _api(client, email, GOOD).status_code == 200          # 4 + 4 never made 5 in a row


def test_old_failures_do_not_add_up(client, seeded_app, clock, events):
    email = seeded_app['pro_email']
    _fail(client, email, 4)
    clock.forward(minutes=16)
    _fail(client, email, 1)
    assert _api(client, email, GOOD).status_code == 200


def test_attempts_during_a_lock_do_not_extend_it(client, seeded_app, clock, events):
    """Time-based only: hammering a locked account must not keep it locked."""
    email = seeded_app['su_email']                               # an SU admin, on purpose
    _fail(client, email, 5)
    clock.forward(minutes=10)
    _fail(client, email, 20)
    clock.forward(minutes=5, seconds=1)                          # 15 min after the lock began
    assert _api(client, email, seeded_app['su_pw']).status_code == 200


def test_unknown_wrong_and_locked_are_indistinguishable(client, seeded_app, clock, events, monkeypatch):
    import src.services.auth_service as auth_service
    email = seeded_app['pro_email']
    hashes = []
    real = auth_service.verify_password
    monkeypatch.setattr(auth_service, 'verify_password',
                        lambda pw, h: (hashes.append(h), real(pw, h))[1])

    def answers(e, pw):
        reset_rate_limits()
        a = _api(client, e, pw)
        h = _html(client, e, pw)
        # The CSRF token is random per rendering; everything else is compared.
        page = re.sub(r'name="csrf_token" value="[^"]*"', 'CSRF',
                      h.get_data(as_text=True))
        return (a.status_code, a.get_json(), h.status_code, page.replace(e, 'EMAIL'))

    unknown = answers('nobody@test.com', 'whatever-pw')
    assert len(hashes) == 2            # bcrypt was run for an account that does not exist
    wrong = answers(email, 'wrong-password')
    _fail(client, email, 5)
    n = len(hashes)
    locked_wrong = answers(email, 'wrong-password')
    locked_right = answers(email, GOOD)
    assert len(hashes) == n + 4        # and for a locked account too
    assert unknown == wrong == locked_wrong == locked_right
    assert unknown[0] == 401
    # Guessing unknown addresses leaves no rows behind.
    with client.application.app_context():
        s = get_session()
        assert s.query(LoginLockout).count() == 1
        s.close()


def test_html_login_is_rate_limited_and_the_page_is_not(client, seeded_app, clock, events):
    for _ in range(30):
        assert client.get('/login').status_code == 200           # loading the page is free
    codes = [_html(client, 'nobody@test.com', 'x').status_code for _ in range(21)]
    assert codes[:20] == [200] * 20
    assert codes[20] == 429


def test_su_unlock_is_su_only_and_audited(client, seeded_app, clock, events):
    email, guid = seeded_app['pro_email'], seeded_app['pro_guid']
    su = {'Authorization': 'Bearer ' + seeded_app['su_token']}
    pro = {'Authorization': 'Bearer ' + seeded_app['pro_token']}
    _fail(client, email, 5)
    listed = client.get('/api/admin/login-locks', headers=su)
    assert listed.status_code == 200
    assert [(x['user_guid'], x['email']) for x in listed.get_json()] == [(guid, email)]
    assert client.get('/api/admin/login-locks', headers=pro).status_code == 403
    url = f'/api/admin/users/{guid}/unlock-login'
    assert client.post(url, headers=pro).status_code == 403
    assert client.post(url).status_code == 401
    assert _api(client, email, GOOD).status_code == 401          # still locked
    r = client.post(url, headers=su)
    assert r.status_code == 200 and r.get_json()['was_locked'] is True
    assert _api(client, email, GOOD).status_code == 200
    assert client.get('/api/admin/login-locks', headers=su).get_json() == []
    ev = [e for e in events if e['event'] == 'login_unlock']
    assert len(ev) == 1 and ev[0]['user_guid'] == seeded_app['su_guid']
    assert ev[0]['detail']['target_guid'] == guid


def test_the_server_side_unlock_function(client, seeded_app, clock, events):
    """What scripts/unlock_login.py calls: the way out when no SU can log in."""
    email = seeded_app['su_email']
    _fail(client, email, 5)
    assert _api(client, email, seeded_app['su_pw']).status_code == 401
    with client.application.app_context():
        s = get_session()
        assert login_lockout.unlock(s, seeded_app['su_guid']) is True
        s.commit()
        s.close()
    assert _api(client, email, seeded_app['su_pw']).status_code == 200
