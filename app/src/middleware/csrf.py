"""CSRF protection — wraps Flask-WTF CSRFProtect.
Active on all form POST endpoints. API endpoints using Bearer tokens
are exempt (stateless auth does not need CSRF)."""
from flask_wtf.csrf import CSRFProtect

csrf = CSRFProtect()


def init_csrf(app):
    """Initialise CSRF protection on the Flask app."""
    csrf.init_app(app)
