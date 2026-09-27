"""Verifies a Google Identity Services ID token server-side.

The frontend uses Google's client-side "Sign in with Google" button (Google
Identity Services), not a server-redirect OAuth flow -- there's no
authorization code to exchange and no client secret involved. Google hands
the frontend a signed JWT (the "ID token") directly; this module is the one
piece of server work that flow still needs: proving that JWT was really
signed by Google, for this app specifically, and hasn't expired.

Deliberately built on `pyjwt` (already a dependency for our own tokens, see
core/security.py) rather than adding the `google-auth` package -- verifying
an RS256-signed JWT against Google's published public keys is exactly what
PyJWKClient already does, and Google's ID tokens are documented, stable
JWTs, so there's no real benefit to the heavier, Google-specific library
for this one call.
"""

from __future__ import annotations

from typing import Any

import jwt
from jwt import PyJWKClient

from app.core.exceptions import UnauthorizedError

# Google publishes its current signing keys here, rotating them periodically;
# PyJWKClient re-fetches (and caches) them on demand rather than us pinning
# specific keys.
_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"

# Both forms show up in the wild across Google's own docs/tokens.
_VALID_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}

# Cache fetched keys for an hour rather than hitting Google's JWKS endpoint
# on every single login -- keys rotate on the order of weeks, not minutes.
_jwks_client = PyJWKClient(_JWKS_URL, cache_keys=True, lifespan=3600)


def verify_google_id_token(id_token: str, *, client_id: str) -> dict[str, Any]:
    """Returns the token's decoded claims if it's a valid, current Google ID
    token issued for `client_id`. Raises UnauthorizedError otherwise -- every
    failure mode (bad signature, wrong audience, expired, wrong issuer)
    collapses to the same "your Google sign-in didn't check out" response;
    the specifics only matter for our own logs, not for what the client
    needs to react to.
    """
    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(id_token)
        payload = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=client_id,
            options={"require": ["exp", "iat", "aud", "iss", "sub", "email"]},
        )
    except jwt.PyJWTError as exc:
        raise UnauthorizedError("Invalid Google sign-in token.") from exc

    if payload.get("iss") not in _VALID_ISSUERS:
        raise UnauthorizedError("Invalid Google sign-in token.")

    # Google only ever issues a token for an address it has verified control
    # of -- this should always be true, but a candidate account is exactly
    # the kind of thing worth double-checking rather than assuming.
    if not payload.get("email_verified"):
        raise UnauthorizedError("Google account email is not verified.")

    return payload
