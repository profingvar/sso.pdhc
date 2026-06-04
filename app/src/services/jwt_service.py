"""JWT service — issue, decode, validate tokens. Checks revocation."""
import uuid
from datetime import datetime, timedelta, timezone

import jwt


class TokenExpiredError(Exception):
    pass


class TokenInvalidError(Exception):
    pass


class TokenRevokedError(Exception):
    pass


def issue_token(user_guid, secret_key, expiry_hours=24):
    """Issue a JWT with user_guid as subject and a unique token_guid (jti).

    Also embeds a stable per-session id (``sid``) claim — same value
    on every /me / /me/service call validated against this token; new
    login → new token → fresh sid (ticket #191, Lag 2022:913
    chain-of-custody correlation). Logout revokes the token, which
    also retires its sid.
    """
    now = datetime.now(timezone.utc)
    payload = {
        'sub': user_guid,
        'jti': str(uuid.uuid4()),
        'sid': str(uuid.uuid4()),
        'iat': now,
        'exp': now + timedelta(hours=expiry_hours),
    }
    return jwt.encode(payload, secret_key, algorithm='HS256')


def decode_token(token, secret_key):
    """Decode and validate a JWT. Returns payload dict.
    Raises TokenExpiredError or TokenInvalidError."""
    try:
        payload = jwt.decode(token, secret_key, algorithms=['HS256'])
        return payload
    except jwt.ExpiredSignatureError:
        raise TokenExpiredError("Token has expired")
    except jwt.InvalidTokenError as e:
        raise TokenInvalidError(f"Invalid token: {e}")


def validate_token(token, secret_key, session):
    """Full validation: decode + check revocation. Returns payload.
    Raises TokenExpiredError, TokenInvalidError, or TokenRevokedError.

    Revocation is checked in two ways:
    1. Per-token: RevokedToken (logout, targeted revoke).
    2. Per-user: User.token_revocation_epoch — any token whose iat precedes
       this epoch is considered flushed (ticket #44 bulk session flush).
    """
    payload = decode_token(token, secret_key)

    from src.models.revoked_token import RevokedToken
    token_guid = payload.get('jti')
    if token_guid:
        revoked = session.query(RevokedToken).filter_by(token_guid=token_guid).first()
        if revoked:
            raise TokenRevokedError("Token has been revoked")

    # Ticket #44: bulk session flush via user-level epoch.
    # We do the user lookup here (one extra SELECT) so every path that calls
    # validate_token (API middleware + frontend session loader) honours the
    # flush uniformly — no risk of one caller forgetting the check.
    user_guid = payload.get('sub')
    token_iat = payload.get('iat')
    if user_guid and token_iat is not None:
        from src.models.user import User
        user = session.query(User).filter_by(guid=user_guid).first()
        if user is not None and getattr(user, 'token_revocation_epoch', None) is not None:
            epoch = user.token_revocation_epoch
            # pyjwt stores iat as Unix seconds on encode.
            token_iat_dt = datetime.fromtimestamp(int(token_iat), tz=timezone.utc)
            # Compare in UTC. DB-stored DateTime may be naive (Postgres
            # `timestamp without time zone`) — assume UTC in that case.
            if epoch.tzinfo is None:
                epoch = epoch.replace(tzinfo=timezone.utc)
            if token_iat_dt < epoch:
                raise TokenRevokedError("Token predates user's revocation epoch")

    return payload


def revoke_token(token_guid, expires_at, session):
    """Add token to revocation list."""
    from src.models.revoked_token import RevokedToken
    revoked = RevokedToken(token_guid=token_guid, expires_at=expires_at)
    session.add(revoked)


def prune_expired_tokens(session):
    """Remove revoked tokens that have passed their expiry."""
    from src.models.revoked_token import RevokedToken
    now = datetime.now(timezone.utc)
    session.query(RevokedToken).filter(RevokedToken.expires_at < now).delete()
