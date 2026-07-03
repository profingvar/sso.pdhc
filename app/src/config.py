"""Application configuration — loads .env, validates required vars."""
import os
from dotenv import load_dotenv

load_dotenv()

REQUIRED_VARS = ['SECRET_KEY', 'DATABASE_URL']


class Config:
    """Typed config object loaded from environment."""

    SECRET_KEY = os.environ.get('SECRET_KEY', '')
    DATABASE_URL = os.environ.get('DATABASE_URL', '')
    SESSION_EXPIRY_HOURS = int(os.environ.get('SESSION_EXPIRY_HOURS', '24'))
    FLASK_ENV = os.environ.get('FLASK_ENV', 'production')
    LOG_DIR = os.environ.get('LOG_DIR', './logs')

    # SSO handshake
    ALLOWED_ORIGINS = [o.strip() for o in os.environ.get('ALLOWED_ORIGINS', '').split(',') if o.strip()]
    ALLOWED_CALLBACK_URLS = [u.strip() for u in os.environ.get('ALLOWED_CALLBACK_URLS', '').split(',') if u.strip()]

    # Bootstrap SU
    BOOTSTRAP_SU_EMAIL = os.environ.get('BOOTSTRAP_SU_EMAIL', '')
    BOOTSTRAP_SU_PASSWORD = os.environ.get('BOOTSTRAP_SU_PASSWORD', '')

    # API key metadata
    KEY_CREATED_AT = os.environ.get('KEY_CREATED_AT', '')

    # Internal service-to-service key (shared with request.pdhc, contract.pdhc)
    INTERNAL_SERVICE_KEY = os.environ.get('INTERNAL_SERVICE_KEY', '')

    # Health check polling interval (seconds) — 300 = 5 min for dev, increase later
    HEALTH_CHECK_INTERVAL = int(os.environ.get('HEALTH_CHECK_INTERVAL', '1800'))

    # Service credentials: collected as dict {client_id: secret}
    SERVICE_CREDENTIALS = {}

    # Keyed services: collected as dict {service_name: {url, key}}
    # Populated from KEYAUTH_SERVICE_<NAME>_URL / KEYAUTH_SERVICE_<NAME>_KEY env pairs
    KEYED_SERVICES = {}

    def __init__(self):
        # Collect SSO_CLIENT_ID_* / SSO_CLIENT_SECRET_* pairs
        for key, value in os.environ.items():
            if key.startswith('SSO_CLIENT_ID_'):
                name = key[len('SSO_CLIENT_ID_'):]
                secret_key = f'SSO_CLIENT_SECRET_{name}'
                secret = os.environ.get(secret_key, '')
                if value and secret:
                    self.SERVICE_CREDENTIALS[value] = secret

        # Collect KEYAUTH_SERVICE_<NAME>_URL / _KEY pairs
        seen = set()
        for key in os.environ:
            if key.startswith('KEYAUTH_SERVICE_') and key.endswith('_URL'):
                name = key[len('KEYAUTH_SERVICE_'):-len('_URL')].lower()
                if name in seen:
                    continue
                seen.add(name)
                url = os.environ.get(key, '').rstrip('/')
                svc_key = os.environ.get(f'KEYAUTH_SERVICE_{name.upper()}_KEY', '')
                if url and svc_key:
                    self.KEYED_SERVICES[name] = {'url': url, 'key': svc_key}

    @classmethod
    def validate(cls):
        """Raise on missing required vars. Call at startup."""
        missing = [v for v in REQUIRED_VARS if not os.environ.get(v)]
        if missing:
            raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")


class TestConfig(Config):
    """Config override for testing — no DB required."""
    TESTING = True
    SECRET_KEY = 'test-secret-key-not-for-production'
    DATABASE_URL = 'sqlite://'
    SESSION_EXPIRY_HOURS = 1
    ALLOWED_ORIGINS = ['http://localhost:9000']
    ALLOWED_CALLBACK_URLS = ['http://localhost:9000/callback']
    SERVICE_CREDENTIALS = {'test-client-id': 'test-client-secret'}
    LOG_DIR = '/tmp/sso_test_logs'
