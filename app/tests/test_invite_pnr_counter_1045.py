"""#1045 (internal security review) — the invite's wrong-personnummer
counter must not lose a concurrent failure.

`check_invite_personnummer` used to read `pnr_failures` into Python, add one
and write the sum back. The row had already been loaded (by
`find_valid_invite`) BEFORE the eps call, so a failure counted by another
gunicorn worker while this request waited on eps was overwritten: N parallel
wrong guesses could cost the guesser one failure instead of N, and the
five-try lockout (MAX_PNR_FAILURES) did not bound how many personnummer a
holder of the link could try.

The test makes the race deterministic: while this request is "at eps", a
concurrent worker's failures are committed straight to the row (raw SQL, so
this request's loaded object stays stale, exactly as another process's write
would). The count afterwards must include them. Synthetic numbers only.
"""
from sqlalchemy import text

from src.db import get_db
from src.models.patient_invite_detail import PatientInviteDetail
from src.db import get_session
from tests.test_patient_invite_1050 import WRONG, _accept, _token
from tests.test_patient_app_1040 import events, world  # noqa: F401  (fixtures)


def _failures(world, invite_guid):
    with world['app'].app_context():
        s = get_session()
        n = s.query(PatientInviteDetail).filter_by(invite_guid=invite_guid).one().pnr_failures
        s.close()
    return n


def test_a_failure_counted_by_another_worker_during_the_eps_call_is_kept(world, monkeypatch):
    app, eps = world['app'], world['eps']
    token, body = _token(world)
    guid = body['invite_guid']
    real_post = eps.post

    def post_while_others_fail(url, headers, json_body, timeout):
        # Three wrong guesses from other workers land while this one waits.
        get_db().execute(text(
            'UPDATE patient_invite_details SET pnr_failures = pnr_failures + 3 '
            'WHERE invite_guid = :g'), {'g': guid})
        return real_post(url, headers, json_body, timeout)

    monkeypatch.setattr('src.services.eps_client._http_post', post_while_others_fail)
    r = _accept(app.test_client(), token, pnr=WRONG)
    assert r.status_code == 400
    assert _failures(world, guid) == 4          # 3 concurrent + this one


def test_the_concurrent_failures_close_the_invite_at_the_limit(world, monkeypatch):
    app, eps = world['app'], world['eps']
    token, body = _token(world)
    guid = body['invite_guid']
    real_post = eps.post

    def post_while_others_fail(url, headers, json_body, timeout):
        get_db().execute(text(
            'UPDATE patient_invite_details SET pnr_failures = pnr_failures + 4 '
            'WHERE invite_guid = :g'), {'g': guid})
        return real_post(url, headers, json_body, timeout)

    monkeypatch.setattr('src.services.eps_client._http_post', post_while_others_fail)
    r = _accept(app.test_client(), token, pnr=WRONG)
    # the fifth failure closes it, whichever worker counted the other four
    assert r.status_code == 404
    assert app.test_client().get('/patient-invite/' + token).status_code == 404
