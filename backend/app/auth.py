"""Section 3: Clerk session-token verification (PLAN.md section 3's locked
auth decision — Clerk, not hand-rolled). Clerk's React SDK on the frontend
owns sign-up, log-in, session issuance, password reset, email verification,
and Google OAuth entirely; the backend never sees a password or handles
any of that flow. All this module does is verify the Bearer JWT Clerk
attaches to every frontend request and extract the Clerk user id (`sub`)
that the rest of the app (seen_questions, performance_counters,
quiz_attempts — all already keyed on a TEXT user_id, Section 2) uses.

Verification is signature + expiry + issuer, against Clerk's own published
JWKS — no Clerk API call needed per request, and no secret key required
for verification (only CLERK_ISSUER, which is public information: the
Clerk Frontend API URL for your app instance).
"""

from __future__ import annotations

import os
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import load_dotenv

load_dotenv()

CLERK_ISSUER = os.environ.get("CLERK_ISSUER")  # e.g. https://your-app.clerk.accounts.dev

_bearer_scheme = HTTPBearer(auto_error=False)


@lru_cache(maxsize=1)
def _jwks_client() -> jwt.PyJWKClient:
    """Cached JWKS client — PyJWT internally caches individual keys too,
    so this avoids a network round-trip on every request without any
    manual TTL bookkeeping."""
    if not CLERK_ISSUER:
        raise RuntimeError(
            "CLERK_ISSUER is not set in backend/.env — copy your Clerk app's "
            "Frontend API URL there (Clerk dashboard -> API Keys)."
        )
    return jwt.PyJWKClient(f"{CLERK_ISSUER}/.well-known/jwks.json")


class InvalidSessionToken(Exception):
    pass


def verify_session_token(token: str, jwks_client: jwt.PyJWKClient | None = None) -> str:
    """Verifies a Clerk session JWT's signature, expiry, and issuer;
    returns the Clerk user id (the `sub` claim). Raises InvalidSessionToken
    on any failure — signature mismatch, expired, wrong issuer, malformed."""
    client = jwks_client or _jwks_client()
    try:
        signing_key = client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=CLERK_ISSUER,
            options={"require": ["exp", "iat", "sub"], "verify_aud": False},
        )
    except Exception as e:  # noqa: BLE001 — any jwt/jwks failure is "invalid token" to the caller
        raise InvalidSessionToken(str(e)) from e
    return payload["sub"]


def get_current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> str:
    """FastAPI dependency for protected routes — raises 401 on any missing
    or invalid token, never lets an unauthenticated request through."""
    if credentials is None or not credentials.credentials:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    try:
        return verify_session_token(credentials.credentials)
    except InvalidSessionToken as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid session token: {e}") from e
