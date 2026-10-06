from __future__ import annotations

import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.exceptions import UnauthorizedError
from app.modules.auth import google_oauth

CLIENT_ID = "test-client-id.apps.googleusercontent.com"


@pytest.fixture
def keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _make_token(private_key, **overrides) -> str:
    now = int(time.time())
    payload = {
        "iat": now,
        "exp": now + 3600,
        "aud": CLIENT_ID,
        "iss": "https://accounts.google.com",
        "sub": "1234567890",
        "email": "candidate@example.com",
        "email_verified": True,
        **overrides,
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


def _patch_jwks(monkeypatch, public_key) -> None:
    # Stands in for the real network call to Google's JWKS endpoint: what
    # matters here is the verification logic (signature, audience, issuer,
    # email_verified), not PyJWKClient's own HTTP/caching behavior.
    monkeypatch.setattr(
        google_oauth._jwks_client,
        "get_signing_key_from_jwt",
        lambda token: SimpleNamespace(key=public_key),
    )


def test_accepts_a_valid_token(monkeypatch, keypair):
    private_key, public_key = keypair
    _patch_jwks(monkeypatch, public_key)

    claims = google_oauth.verify_google_id_token(
        _make_token(private_key), client_id=CLIENT_ID
    )

    assert claims["email"] == "candidate@example.com"
    assert claims["sub"] == "1234567890"


def test_rejects_wrong_audience(monkeypatch, keypair):
    private_key, public_key = keypair
    _patch_jwks(monkeypatch, public_key)
    token = _make_token(private_key, aud="someone-elses-client-id")

    with pytest.raises(UnauthorizedError):
        google_oauth.verify_google_id_token(token, client_id=CLIENT_ID)


def test_rejects_wrong_issuer(monkeypatch, keypair):
    private_key, public_key = keypair
    _patch_jwks(monkeypatch, public_key)
    token = _make_token(private_key, iss="https://evil.example.com")

    with pytest.raises(UnauthorizedError):
        google_oauth.verify_google_id_token(token, client_id=CLIENT_ID)


def test_rejects_unverified_email(monkeypatch, keypair):
    private_key, public_key = keypair
    _patch_jwks(monkeypatch, public_key)
    token = _make_token(private_key, email_verified=False)

    with pytest.raises(UnauthorizedError):
        google_oauth.verify_google_id_token(token, client_id=CLIENT_ID)


def test_rejects_expired_token(monkeypatch, keypair):
    private_key, public_key = keypair
    _patch_jwks(monkeypatch, public_key)
    now = int(time.time())
    token = _make_token(private_key, iat=now - 7200, exp=now - 3600)

    with pytest.raises(UnauthorizedError):
        google_oauth.verify_google_id_token(token, client_id=CLIENT_ID)


def test_rejects_token_signed_by_a_different_key(monkeypatch, keypair):
    # The signature check itself: a token that's otherwise well-formed but
    # signed by a key that isn't the one Google's JWKS says it should be.
    private_key, _real_public_key = keypair
    attacker_public_key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048
    ).public_key()
    _patch_jwks(monkeypatch, attacker_public_key)
    token = _make_token(private_key)

    with pytest.raises(UnauthorizedError):
        google_oauth.verify_google_id_token(token, client_id=CLIENT_ID)
