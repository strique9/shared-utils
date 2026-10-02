"""Who is signed in. Cloudflare Access authenticates; this module verifies its proof.

Every request that reaches an app through the tunnel carries a `Cf-Access-Jwt-Assertion`
header (or a `CF_Authorization` cookie): a JWT signed by Cloudflare for that app's Access
application. We verify the signature against the team's published keys, the application's
Audience tag (AUD) and the team issuer, and return the claims.

Identity only. What a person may see is each app's own rule, kept in that app.
No web framework here: apps turn `AccessError` into their own HTTP response.

    from shared_utils.access import AccessError, token_from_request, verify_access_token

    token = token_from_request(request.headers, request.cookies)
    claims = verify_access_token(token, team="thebinnacle", aud=ACCESS_AUD)
    email = claims.get("email")

Requires the `access` extra: pip install -e "../shared-utils[access]"
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache

import jwt

JWT_HEADER = "Cf-Access-Jwt-Assertion"
JWT_COOKIE = "CF_Authorization"
ALGORITHMS = ["RS256"]


class AccessError(Exception):
    """A Cloudflare Access token that did not verify."""


def team_domain(team: str) -> str:
    return f"https://{team}.cloudflareaccess.com"


@lru_cache(maxsize=8)
def _jwks_client(team: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"{team_domain(team)}/cdn-cgi/access/certs", cache_keys=True)


def token_from_request(headers: Mapping[str, str], cookies: Mapping[str, str]) -> str | None:
    """The Access JWT a request carries: the header first, then the cookie."""
    return headers.get(JWT_HEADER) or cookies.get(JWT_COOKIE)


def verify_access_token(token: str, *, team: str, aud: str) -> dict:
    """Claims of a Cloudflare Access JWT for the application `aud`, or AccessError."""
    try:
        key = _jwks_client(team).get_signing_key_from_jwt(token)
        return jwt.decode(token, key.key, algorithms=ALGORITHMS, audience=aud, issuer=team_domain(team))
    except jwt.PyJWTError as e:
        raise AccessError(f"Cloudflare Access token rejected: {e}") from e
