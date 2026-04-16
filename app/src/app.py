"""Flask application factory — registers blueprints, DB, config, middleware."""
import logging
import time

from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException

from src.config import Config, TestConfig
from src.db import init_db, close_db
import src.db as _db
from src.middleware.csrf import init_csrf
from src.middleware.cors import init_cors
from src.services.audit_log import init_audit_log

_start_time = None


def create_app(config_override=None):
    """Create and configure the Flask application."""
    global _start_time
    _start_time = time.time()

    app = Flask(__name__)

    # Load config
    if config_override and config_override.get('TESTING'):
        app.config.from_object(TestConfig())
    else:
        cfg = Config()
        Config.validate()
        app.config.from_mapping(
            SECRET_KEY=cfg.SECRET_KEY,
            DATABASE_URL=cfg.DATABASE_URL,
            SESSION_EXPIRY_HOURS=cfg.SESSION_EXPIRY_HOURS,
            FLASK_ENV=cfg.FLASK_ENV,
            LOG_DIR=cfg.LOG_DIR,
            ALLOWED_ORIGINS=cfg.ALLOWED_ORIGINS,
            ALLOWED_CALLBACK_URLS=cfg.ALLOWED_CALLBACK_URLS,
            SERVICE_CREDENTIALS=cfg.SERVICE_CREDENTIALS,
            INTERNAL_SERVICE_KEY=cfg.INTERNAL_SERVICE_KEY,
            WTF_CSRF_ENABLED=True,
            WTF_CSRF_SSL_STRICT=False,
        )

    # Apply overrides (for testing)
    if config_override:
        app.config.update(config_override)

    # Initialise database
    db_url = app.config.get('DATABASE_URL', '')
    if db_url:
        init_db(db_url)
        # Import models to register with Base.metadata
        import src.models  # noqa: F401
        if db_url == 'sqlite://':
            # In-memory SQLite for testing: create tables immediately
            from src.db import create_all_tables
            create_all_tables()

    # Teardown: commit/rollback session per request
    app.teardown_appcontext(close_db)

    # Trust reverse proxy headers (X-Forwarded-For, X-Forwarded-Proto)
    if not app.config.get('TESTING'):
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    # Cookie security
    if not app.config.get('TESTING'):
        is_dev = app.config.get('FLASK_ENV') == 'development'
        app.config['SESSION_COOKIE_SECURE'] = not is_dev
        app.config['SESSION_COOKIE_HTTPONLY'] = True
        app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

    # Security headers
    @app.after_request
    def set_security_headers(response):
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        if not app.config.get('TESTING'):
            response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        return response

    # Max upload size (16 MB) to prevent CSV DoS
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

    # Middleware
    init_csrf(app)
    init_cors(app)

    # Audit logging
    log_dir = app.config.get('LOG_DIR', './logs')
    init_audit_log(log_dir)

    # --- Register blueprints ---
    from src.routes.auth import auth_bp
    app.register_blueprint(auth_bp)

    from src.routes.patient import patient_bp
    app.register_blueprint(patient_bp)

    from src.routes.groups import groups_bp
    app.register_blueprint(groups_bp)

    from src.routes.admin import admin_bp
    app.register_blueprint(admin_bp)

    from src.routes.public import public_bp
    app.register_blueprint(public_bp)

    from src.routes.frontend import frontend_bp
    app.register_blueprint(frontend_bp)

    from src.fhir.capability_statement import fhir_bp
    app.register_blueprint(fhir_bp)

    from src.routes.internal import internal_bp
    app.register_blueprint(internal_bp)

    # Exempt API blueprints from CSRF — they use Bearer tokens, not cookies
    from src.middleware.csrf import csrf
    for bp in [auth_bp, patient_bp, groups_bp, admin_bp, public_bp, fhir_bp, internal_bp]:
        csrf.exempt(bp)

    # --- Error handlers: catch exceptions and log them ---
    @app.errorhandler(Exception)
    def handle_exception(e):
        # Let werkzeug HTTPException subclasses (404, 405, 403, etc.) keep
        # their native status + response instead of being coerced into 500.
        # Ticket #58: previously any NotFound raised by routing fell through
        # to the generic 500 branch, which broke test_docs_download_unknown_file_404.
        if isinstance(e, HTTPException):
            return e
        app.logger.exception("Unhandled exception: %s", e)
        return jsonify({"error": "internal_error", "message": str(e)}), 500

    @app.errorhandler(500)
    def handle_500(e):
        app.logger.exception("500 error: %s", e)
        return jsonify({"error": "internal_error", "message": "Internal server error"}), 500

    # --- Configure logging for production ---
    if not app.config.get('TESTING'):
        log_dir = app.config.get('LOG_DIR', './logs')
        import os
        os.makedirs(log_dir, exist_ok=True)
        file_handler = logging.FileHandler(os.path.join(log_dir, 'app.log'))
        file_handler.setLevel(logging.WARNING)
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)s %(name)s: %(message)s'
        ))
        app.logger.addHandler(file_handler)
        app.logger.setLevel(logging.INFO)
        # Also log to stderr for gunicorn capture
        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(logging.WARNING)
        app.logger.addHandler(stream_handler)

    # --- Health endpoint (Phase 3.j) ---
    @app.route('/api/health')
    def health():
        db_ok = False
        if _db.engine is not None:
            try:
                with _db.engine.connect() as conn:
                    conn.execute(conn.default_schema_name if False else __import__('sqlalchemy').text('SELECT 1'))
                db_ok = True
            except Exception:
                db_ok = False

        uptime = round(time.time() - _start_time, 1) if _start_time else 0

        status = 'ok' if db_ok or app.config.get('TESTING') else 'degraded'
        code = 200 if status == 'ok' else 503

        return jsonify({
            "status": status,
            "database": "connected" if db_ok else "unavailable",
            "uptime_seconds": uptime,
        }), code

    # --- Service health aggregator (checks all registered services) ---
    _health_cache = {'results': None, 'checked_at': 0}

    def _check_one_service(svc, session, is_self=False):
        """Check a single service's API health and frontend. Runs in a thread."""
        import requests as _requests

        name = svc.get('service_name', '')
        health_url = svc.get('api_health_url', '')
        service_url = svc.get('service_url', '')

        entry = {
            'service_name': name,
            'api': 'unknown',
            'db': 'unknown',
            'frontend': 'unknown',
        }

        # SSO checking its own URLs deadlocks (single gunicorn worker is busy
        # serving this request). Report self as ok since we're clearly running.
        if is_self:
            entry['api'] = 'ok'
            entry['db'] = 'ok'
            entry['frontend'] = 'ok'
            return entry

        if health_url and health_url != '\u2014':
            try:
                r = session.get(health_url, timeout=5)
                if r.ok:
                    entry['api'] = 'ok'
                    try:
                        data = r.json()
                        db_status = data.get('database', '')
                        if db_status == 'connected':
                            entry['db'] = 'ok'
                        elif db_status == 'unavailable':
                            entry['db'] = 'down'
                        elif data.get('status') == 'ok':
                            entry['db'] = 'ok'
                    except Exception:
                        pass
                else:
                    entry['api'] = 'down'
                    entry['db'] = 'unknown'
            except _requests.exceptions.ConnectionError:
                entry['api'] = 'down'
                entry['db'] = 'unknown'
            except _requests.exceptions.Timeout:
                entry['api'] = 'timeout'
                entry['db'] = 'unknown'
            except Exception:
                entry['api'] = 'error'

        # Don't follow redirects — other services redirect to SSO for login,
        # which deadlocks back to this single worker. A 2xx or 3xx means alive.
        if service_url and service_url != '\u2014':
            try:
                r = session.get(service_url, timeout=5, allow_redirects=False)
                entry['frontend'] = 'ok' if r.status_code < 500 else 'down'
            except _requests.exceptions.ConnectionError:
                entry['frontend'] = 'down'
            except _requests.exceptions.Timeout:
                entry['frontend'] = 'timeout'
            except Exception:
                entry['frontend'] = 'error'

        return entry

    @app.route('/api/service-health')
    def service_health():
        import csv as _csv
        import os as _os
        import requests as _requests
        from concurrent.futures import ThreadPoolExecutor

        # Serve cached results if fresh (< 30 seconds old)
        cache_max_age = 30
        now = time.time()
        if _health_cache['results'] and (now - _health_cache['checked_at']) < cache_max_age:
            return jsonify({
                'services': _health_cache['results'],
                'interval_seconds': app.config.get('HEALTH_CHECK_INTERVAL', 300),
                'checked_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(int(_health_cache['checked_at']))),
                'cached': True,
            })

        oath_path = _os.path.join(app.root_path, '..', 'oath_overview.csv')
        services = []
        if _os.path.exists(oath_path):
            with open(oath_path, 'r') as f:
                services = list(_csv.DictReader(f))

        # Check all services in parallel using a shared session (connection pooling)
        session = _requests.Session()
        with ThreadPoolExecutor(max_workers=len(services) or 1) as pool:
            results = list(pool.map(
                lambda svc: _check_one_service(svc, session, is_self=(svc.get('service_name') == 'sso.pdhc')),
                services,
            ))
        session.close()

        _health_cache['results'] = results
        _health_cache['checked_at'] = now

        interval = app.config.get('HEALTH_CHECK_INTERVAL', 300)
        return jsonify({
            'services': results,
            'interval_seconds': interval,
            'checked_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        })

    return app
