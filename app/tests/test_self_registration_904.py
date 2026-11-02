"""#904 — patient self-enrolment: what it is, and what not to "fix".

#904 was filed with its uncertainty stated, because the last security
ticket filed here from configuration alone had to be withdrawn
(#844 -> #845). It asked three things, in order, and said everything
depended on the first: **is patient self-enrolment intended in production
at all?**

MEASURED 2026-11-02, and the UI answers it. `frontend.register_patient`
serves `register_patient.html` at `/register-patient`, its docstring reads
"Patient self-registration", and it had **no decorator at all** — not even
a rate limit, while the equivalent JSON route carried `@rate_limit(10/60s)`.
So self-enrolment is intended, by two anonymous paths, and the one with a
UI was the less protected of the two.

That makes #904's step 2 — "implement the flag the docstring promises,
default closed" — the WRONG fix. It would break a real feature. The
docstring's promise was still a lie and is deleted; what replaces the gate
is a matching rate limit, not a gate.

== THE THING THIS FILE EXISTS TO PREVENT ==

#904 observed that no checksum validation exists, neutrally. Acting on
that observation would be a defect: the platform REQUIRES synthetic
personnummer to carry an INVALID checksum (#405 confinement), so a Luhn
check would reject every synthetic patient the platform creates on
purpose — including `test_patient.py`'s own `199001011234` and
`200101011234`, both of which fail Luhn by design.

So `test_an_invalid_checksum_is_ACCEPTED` is not a lax test. It is the
confinement rule made enforceable, and it fails the moment somebody adds
the "obvious" hardening.
"""
import re
import uuid

import pytest

from src.app import create_app
from src.db import get_session
from src.models.organisation import Organisation

SECRET = 'test-secret-key-not-for-production'


def _luhn_ok(pnr12: str) -> bool:
    """True when the 12-digit personnummer's check digit is correct."""
    ten = pnr12[2:]
    body, check = ten[:9], int(ten[9])
    total = 0
    for i, ch in enumerate(body):
        d = int(ch) * (2 if i % 2 == 0 else 1)
        total += d - 9 if d > 9 else d
    return (10 - total % 10) % 10 == check


@pytest.fixture
def app():
    from src.middleware.rate_limit import reset_rate_limits
    reset_rate_limits()
    return create_app({
        'TESTING': True, 'SECRET_KEY': SECRET,
        'WTF_CSRF_ENABLED': False, 'SESSION_EXPIRY_HOURS': 1,
        'ALLOWED_ORIGINS': [], 'ALLOWED_CALLBACK_URLS': [],
        'SERVICE_CREDENTIALS': {},
    })


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def org_guid(app):
    with app.app_context():
        session = get_session()
        org = Organisation(guid=str(uuid.uuid4()), name='Test Hospital 904')
        session.add(org)
        session.commit()
        guid = org.guid
        session.close()
        return guid


# ======================================================================
# The confinement rule, made enforceable
# ======================================================================

class TestPersonnummerIsDeliberatelyNotChecksummed:
    def test_the_projects_own_synthetic_numbers_FAIL_luhn(self):
        """The premise. If these ever became valid, the #405 confinement
        would be broken and this whole class would be arguing for the
        wrong thing."""
        for p in ('199001011234', '200101011234'):
            assert not _luhn_ok(p), (
                f"{p} now has a VALID checksum — #405 requires synthetic "
                "personnummer to be invalid, so this is a confinement "
                "regression, not a test failure"
            )

    def test_an_invalid_checksum_is_ACCEPTED_by_the_json_route(
            self, client, org_guid):
        """NOT a lax test. A Luhn check here would reject every synthetic
        patient the platform creates on purpose."""
        # 200202021234 would have been the obvious pick and it HAS a valid
        # checksum — the premise guard below caught that, which is what a
        # premise guard is for.
        pnr = '200303031234'
        assert not _luhn_ok(pnr), "pick a number that fails Luhn"
        r = client.post('/api/patient/register', json={
            'email': f'synthetic-{uuid.uuid4().hex[:8]}@example.test',
            'password': 'password123',
            'personnummer': pnr,
            'organisation_guid': org_guid,
        })
        assert r.status_code == 201, r.get_data(as_text=True)

    def test_the_shape_check_is_still_enforced(self, client, org_guid):
        """Not checksummed is not unvalidated. Twelve digits, exactly."""
        for bad in ('123', '1234567890123', '20010101123x', ''):
            r = client.post('/api/patient/register', json={
                'email': f'x-{uuid.uuid4().hex[:8]}@example.test',
                'password': 'password123',
                'personnummer': bad,
                'organisation_guid': org_guid,
            })
            assert r.status_code == 400, (bad, r.status_code)

    def test_no_luhn_implementation_has_appeared_in_the_routes(self):
        """A source guard, with the docstrings stripped out.

        The first version searched the raw source for "luhn" and
        "checksum" — and failed, because the route's own docstring
        EXPLAINS why there is no checksum. A guard that matches the comment
        explaining the guard is the same instrument error as #843's
        filename search, and I made it twice in this one file.

        So: parse with `ast`, drop every docstring, and search what is
        left. The behavioural test above is the real guard; this one names
        the thing so a failure explains itself.
        """
        import ast
        import inspect
        from src.routes import patient as pat_routes

        tree = ast.parse(inspect.getsource(pat_routes))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
                if (node.body and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                        and isinstance(node.body[0].value.value, str)):
                    node.body[0].value.value = ""
        code = ast.unparse(tree).lower()
        for word in ('luhn', 'checksum', 'check_digit'):
            assert word not in code, (
                f"{word!r} appeared in the patient routes' CODE — see "
                "#405: synthetic personnummer must carry an INVALID "
                "checksum, so a check here rejects the platform's own data"
            )


# ======================================================================
# Step 3: the dead config and the docstring that lied
# ======================================================================

class TestTheDocstringNoLongerPromisesAGateThatDoesNotExist:
    def test_the_dead_config_assignment_is_gone(self):
        import inspect
        from src.routes.patient import register
        src = inspect.getsource(register)
        assert 'config = current_app.config' not in src

    def test_the_docstring_states_the_truth_instead(self):
        """The first version asserted that the phrase "Dev-mode only flag"
        was absent from the docstring — and failed, because the new
        docstring QUOTES the old promise while explaining that it was
        false. Asserting the absence of a phrase cannot distinguish a claim
        from a correction, so this asserts the PROPERTY: the function reads
        no config flag, and the docstring says it is unauthenticated.
        """
        import ast
        import inspect
        from src.routes.patient import register

        doc = register.__doc__ or ''
        assert 'UNAUTHENTICATED BY DESIGN' in doc

        body = ast.parse(inspect.getsource(register).lstrip())
        names = {ast.unparse(n) for n in ast.walk(body)
                 if isinstance(n, ast.Attribute)}
        assert not any('current_app.config' in n for n in names), names

    def test_no_such_flag_exists_anywhere_which_is_why_the_claim_was_wrong(
            self):
        """The measurement that made the docstring a lie rather than a
        stale note."""
        import pathlib
        root = pathlib.Path(__file__).parent.parent / 'src'
        hits = []
        for f in root.rglob('*.py'):
            text = f.read_text()
            for name in ('ALLOW_SELF_REG', 'SELF_ENROL',
                         'SELF_REGISTRATION', 'PATIENT_REGISTER'):
                if name in text:
                    hits.append((str(f.name), name))
        assert hits == [], hits


# ======================================================================
# Both paths exist, both are anonymous, and both are now rate-limited
# ======================================================================

class TestTheTwoEnrolmentPaths:
    def test_the_html_path_exists_and_needs_no_login(self, client):
        r = client.get('/register-patient')
        assert r.status_code == 200, r.status_code

    def test_the_html_path_IS_rate_limited_like_the_json_one(
            self, client, org_guid):
        """#904 measured that it had no decorator at all. Eleven GETs in a
        minute must not all succeed, or the limit is decorative."""
        codes = [client.get('/register-patient').status_code
                 for _ in range(12)]
        assert 429 in codes, (
            f"no 429 in {codes} — the rate limit is not applied"
        )

    def test_the_json_path_is_rate_limited_too(self, client, org_guid):
        codes = []
        for i in range(12):
            r = client.post('/api/patient/register', json={
                'email': f'rl-{i}-{uuid.uuid4().hex[:6]}@example.test',
                'password': 'password123',
                'personnummer': '201010101234',
                'organisation_guid': org_guid,
            })
            codes.append(r.status_code)
        assert 429 in codes, codes

    def test_both_routes_carry_the_SAME_limit(self):
        """Two implementations of one enrolment. If the limits drift, the
        looser one is the real policy."""
        import inspect
        from src.routes import patient as pat_routes, frontend as fe
        want = 'max_requests=10, window_seconds=60'
        assert want in inspect.getsource(pat_routes)
        fe_src = inspect.getsource(fe)
        # the decorator sits immediately above the route
        i = fe_src.find("def register_patient(")
        assert want in fe_src[max(0, i - 400):i]


class TestTheEnumerationOracleIsStillThere:
    """Not fixed, and asserted so it is a known state rather than an
    assumption. Both paths tell an anonymous caller whether a personnummer
    is already enrolled — the JSON one with a 409, the HTML one with a
    flash. Rate-limited now, which raises the cost without removing it.

    Left alone deliberately: a registration form that cannot say "this is
    already registered" cannot be used, and hiding it behind a generic
    error makes the feature worse without making enumeration impossible.
    That is a product decision, not a developer's.
    """

    def test_a_duplicate_personnummer_still_409s(self, client, org_guid):
        pnr = '199909091234'
        first = client.post('/api/patient/register', json={
            'email': f'dup-a-{uuid.uuid4().hex[:6]}@example.test',
            'password': 'password123',
            'personnummer': pnr, 'organisation_guid': org_guid,
        })
        assert first.status_code == 201, first.get_data(as_text=True)
        second = client.post('/api/patient/register', json={
            'email': f'dup-b-{uuid.uuid4().hex[:6]}@example.test',
            'password': 'password123',
            'personnummer': pnr, 'organisation_guid': org_guid,
        })
        assert second.status_code == 409
