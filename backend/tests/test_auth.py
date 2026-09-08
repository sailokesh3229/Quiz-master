"""Section 3: Clerk session-token verification tests. Uses a locally
generated RSA keypair standing in for Clerk's own signing key (via a fake
PyJWKClient) so verification logic is fully tested without needing a live
Clerk account/network call."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth import InvalidSessionToken, verify_session_token

ISSUER = "https://test-app.clerk.accounts.dev"


class _FakeSigningKey:
    def __init__(self, key):
        self.key = key


class _FakeJWKSClient:
    def __init__(self, public_key):
        self._public_key = public_key

    def get_signing_key_from_jwt(self, token):
        return _FakeSigningKey(self._public_key)


@pytest.fixture(scope="module")
def keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _make_token(private_key, *, sub="user_abc123", issuer=ISSUER, exp_delta=3600):
    now = int(time.time())
    payload = {"sub": sub, "iss": issuer, "iat": now, "exp": now + exp_delta}
    return jwt.encode(payload, private_key, algorithm="RS256")


def test_valid_token_returns_user_id(keypair, monkeypatch):
    private_key, public_key = keypair
    monkeypatch.setattr("app.auth.CLERK_ISSUER", ISSUER)
    token = _make_token(private_key)
    user_id = verify_session_token(token, jwks_client=_FakeJWKSClient(public_key))
    assert user_id == "user_abc123"


def test_expired_token_is_rejected(keypair, monkeypatch):
    private_key, public_key = keypair
    monkeypatch.setattr("app.auth.CLERK_ISSUER", ISSUER)
    token = _make_token(private_key, exp_delta=-10)
    with pytest.raises(InvalidSessionToken):
        verify_session_token(token, jwks_client=_FakeJWKSClient(public_key))


def test_wrong_issuer_is_rejected(keypair, monkeypatch):
    private_key, public_key = keypair
    monkeypatch.setattr("app.auth.CLERK_ISSUER", ISSUER)
    token = _make_token(private_key, issuer="https://someone-elses-app.clerk.accounts.dev")
    with pytest.raises(InvalidSessionToken):
        verify_session_token(token, jwks_client=_FakeJWKSClient(public_key))


def test_token_signed_by_a_different_key_is_rejected(keypair, monkeypatch):
    _private_key, public_key = keypair
    monkeypatch.setattr("app.auth.CLERK_ISSUER", ISSUER)
    attacker_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged_token = _make_token(attacker_key)
    with pytest.raises(InvalidSessionToken):
        verify_session_token(forged_token, jwks_client=_FakeJWKSClient(public_key))


def test_missing_sub_claim_is_rejected(keypair, monkeypatch):
    private_key, public_key = keypair
    monkeypatch.setattr("app.auth.CLERK_ISSUER", ISSUER)
    now = int(time.time())
    token = jwt.encode({"iss": ISSUER, "iat": now, "exp": now + 3600}, private_key, algorithm="RS256")
    with pytest.raises(InvalidSessionToken):
        verify_session_token(token, jwks_client=_FakeJWKSClient(public_key))
