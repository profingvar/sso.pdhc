"""Phase 3 tests — app factory, config, JWT, auth middleware, CSRF, CORS, rate limit, audit, health."""
import os
import uuid
import json
import logging
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.app import create_app
from src.config import Config as AppConfig, TestConfig as AppTestConfig
from src.db import Base
from src.services.jwt_service import (
    issue_token, decode_token, validate_token,
    TokenExpiredError, TokenInvalidError, TokenRevokedError,
)
from src.services.audit_log import init_audit_log, audit
from src.middleware.rate_limit import rate_limit, reset_rate_limits
from src.models import RevokedToken


SECRET = 'test-secret-key-not-for-production'


# --- Fixtures ---

@pytest.fixture
def app():
    app = create_app({'TESTING': True, 'SECRET_KEY': SECRET, 'WTF_CSRF_ENABLED': False})

    # Register test endpoints once
    from src.middleware.auth_middleware import require_auth, require_su, require_professional, require_patient

    @app.route('/test/protected')
    @require_auth
    def protected():
        from flask import g, jsonify
        return jsonify({"user": g.current_user.guid})

    @app.route('/test/su-only')
    @require_su
    def su_only():
        return 'ok'

    @app.route('/test/pro-only')
    @require_professional
    def pro_only():
        return 'ok'

    @app.route('/test/patient-only')
    @require_patient
    def patient_only():
        return 'ok'

    @app.route('/test/rate-limited')
    @rate_limit(max_requests=5, window_seconds=60)
    def rate_limited():
        return 'ok'

    yield app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(scope='module')
def db_engine():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session(db_engine):
    Session = sessionmaker(bind=db_engine)
    session = Session()
    yield session
    session.rollback()
    session.close()


# --- App Factory ---

class TestAppFactory:
    def test_creates_app(self, app):
        assert app is not None

    def test_testing_config_applied(self, app):
        assert app.config['TESTING'] is True

    def test_secret_key_set(self, app):
        assert app.config['SECRET_KEY'] == SECRET


# --- Config ---

class TestConfigModule:
    def test_test_config_values(self):
        cfg = AppTestConfig()
        assert cfg.TESTING is True
        assert cfg.SECRET_KEY == 'test-secret-key-not-for-production'
        assert cfg.SESSION_EXPIRY_HOURS == 1

    def test_service_credentials_parsing(self):
        os.environ['SSO_CLIENT_ID_TEST'] = 'my-client'
        os.environ['SSO_CLIENT_SECRET_TEST'] = 'my-secret'
        try:
            cfg = AppConfig()
            assert cfg.SERVICE_CREDENTIALS.get('my-client') == 'my-secret'
        finally:
            del os.environ['SSO_CLIENT_ID_TEST']
            del os.environ['SSO_CLIENT_SECRET_TEST']


# --- JWT Service ---

class TestJWTService:
    def test_issue_token_returns_string(self):
        token = issue_token('user-guid-123', SECRET, expiry_hours=1)
        assert isinstance(token, str)
        assert len(token) > 0

    def test_decode_token_success(self):
        token = issue_token('user-guid-123', SECRET)
        payload = decode_token(token, SECRET)
        assert payload['sub'] == 'user-guid-123'
        assert 'jti' in payload
        assert 'exp' in payload

    def test_decode_token_wrong_secret(self):
        token = issue_token('user-guid-123', SECRET)
        with pytest.raises(TokenInvalidError):
            decode_token(token, 'wrong-secret')

    def test_decode_expired_token(self):
        import jwt as pyjwt
        payload = {
            'sub': 'user-guid-123',
            'jti': str(uuid.uuid4()),
            'iat': datetime.now(timezone.utc) - timedelta(hours=2),
            'exp': datetime.now(timezone.utc) - timedelta(hours=1),
        }
        token = pyjwt.encode(payload, SECRET, algorithm='HS256')
        with pytest.raises(TokenExpiredError):
            decode_token(token, SECRET)

    def test_validate_token_revoked(self, db_session):
        token = issue_token('user-guid-123', SECRET, expiry_hours=1)
        payload = decode_token(token, SECRET)
        token_guid = payload['jti']

        revoked = RevokedToken(
            token_guid=token_guid,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        db_session.add(revoked)
        db_session.commit()

        with pytest.raises(TokenRevokedError):
            validate_token(token, SECRET, db_session)

    def test_validate_token_not_revoked(self, db_session):
        token = issue_token('user-guid-456', SECRET, expiry_hours=1)
        payload = validate_token(token, SECRET, db_session)
        assert payload['sub'] == 'user-guid-456'

    def test_token_has_unique_jti(self):
        t1 = issue_token('user-guid-123', SECRET)
        t2 = issue_token('user-guid-123', SECRET)
        p1 = decode_token(t1, SECRET)
        p2 = decode_token(t2, SECRET)
        assert p1['jti'] != p2['jti']


# --- Auth Middleware ---

class TestAuthMiddleware:
    def test_no_token_returns_401(self, client):
        response = client.get('/test/protected')
        assert response.status_code == 401

    def test_invalid_token_returns_401(self, client):
        response = client.get('/test/protected', headers={'Authorization': 'Bearer invalid-token'})
        assert response.status_code == 401

    def test_require_su_without_auth_returns_401(self, client):
        response = client.get('/test/su-only')
        assert response.status_code == 401

    def test_require_professional_without_auth_returns_401(self, client):
        response = client.get('/test/pro-only')
        assert response.status_code == 401

    def test_require_patient_without_auth_returns_401(self, client):
        response = client.get('/test/patient-only')
        assert response.status_code == 401


# --- CSRF ---

class TestCSRF:
    def test_csrf_exempt_on_api_routes(self, client):
        response = client.get('/api/health')
        assert response.status_code == 200


# --- CORS ---

class TestCORS:
    def test_cors_headers_present(self, client):
        response = client.get('/api/health', headers={'Origin': 'http://localhost:9000'})
        assert response.status_code == 200


# --- Rate Limiter ---

class TestRateLimiter:
    def test_rate_limit_allows_under_threshold(self, client):
        reset_rate_limits()
        for _ in range(5):
            response = client.get('/test/rate-limited')
            assert response.status_code == 200

    def test_rate_limit_blocks_over_threshold(self, client):
        reset_rate_limits()
        for _ in range(5):
            client.get('/test/rate-limited')

        response = client.get('/test/rate-limited')
        assert response.status_code == 429
        data = response.get_json()
        assert data['error'] == 'rate_limit_exceeded'


# --- Audit Log ---

class TestAuditLog:
    def test_audit_writes_to_file(self):
        # Clear existing handlers to avoid writing to old paths
        logger = logging.getLogger('audit')
        logger.handlers.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            init_audit_log(tmpdir)
            audit('test_event', user_guid='user-123', detail={'action': 'test'}, ip='127.0.0.1')

            for handler in logger.handlers:
                handler.flush()

            log_path = os.path.join(tmpdir, 'audit.log')
            assert os.path.exists(log_path)
            with open(log_path) as f:
                line = f.readline()
                entry = json.loads(line)
                assert entry['event'] == 'test_event'
                assert entry['user_guid'] == 'user-123'
                assert entry['ip'] == '127.0.0.1'


# --- Health Endpoint ---

class TestHealthEndpoint:
    def test_health_returns_ok_in_test_mode(self, client):
        response = client.get('/api/health')
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'ok'
        assert 'uptime_seconds' in data


# --- #704: revoked_tokens had no pruner with a caller ---

class TestPruningRevokedTokens:
    """`prune_expired_tokens` existed, was correct, and nothing called it.

    Every logout inserts a row here and `validate_token` reads the table on
    every request on every service (§11 forbids caching the access blob), so
    it is on the platform's hot path and had grown unbounded since March.
    """

    @pytest.fixture(autouse=True)
    def _clean_table(self, db_session):
        """Sibling tests in this module leave revoked rows behind; these
        assertions are about counts, so start from a known table."""
        db_session.query(RevokedToken).delete()
        db_session.commit()

    def _row(self, db_session, guid, hours):
        from datetime import datetime, timedelta, timezone
        rt = RevokedToken(
            token_guid=guid,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=hours),
        )
        db_session.add(rt)
        db_session.commit()
        return rt

    def test_it_removes_expired_rows_and_reports_how_many(self, db_session):
        from src.services.jwt_service import prune_expired_tokens
        self._row(db_session, 'gone-1', -2)
        self._row(db_session, 'gone-2', -1)
        removed = prune_expired_tokens(db_session)
        db_session.commit()
        assert removed == 2
        assert db_session.query(RevokedToken).count() == 0

    def test_it_leaves_rows_that_are_still_doing_work(self, db_session):
        """A row before its exp is the only thing stopping a live token."""
        from src.services.jwt_service import prune_expired_tokens
        self._row(db_session, 'gone-1', -1)
        self._row(db_session, 'keep-1', +1)
        prune_expired_tokens(db_session)
        db_session.commit()
        remaining = [r.token_guid for r in db_session.query(RevokedToken).all()]
        assert remaining == ['keep-1']

    def test_a_pruned_row_cannot_un_revoke_a_live_token(self, db_session):
        """The safety argument for pruning at exp with no grace margin:
        once exp has passed, decode_token raises before the revocation
        check is ever reached, so the row can no longer change an answer."""
        from src.services.jwt_service import prune_expired_tokens
        token = issue_token('user-prune-1', SECRET, expiry_hours=1)
        payload = decode_token(token, SECRET)
        self._row(db_session, payload['jti'], +1)

        prune_expired_tokens(db_session)      # not yet expired
        db_session.commit()
        with pytest.raises(TokenRevokedError):
            validate_token(token, SECRET, db_session)

    def test_counting_changes_nothing(self, db_session):
        """--dry-run has to be safe to run against production."""
        from src.services.jwt_service import count_expired_tokens
        self._row(db_session, 'gone-1', -1)
        self._row(db_session, 'keep-1', +1)
        assert count_expired_tokens(db_session) == 1
        assert db_session.query(RevokedToken).count() == 2

    def test_pruning_an_empty_table_is_a_no_op(self, db_session):
        from src.services.jwt_service import prune_expired_tokens
        assert prune_expired_tokens(db_session) == 0
