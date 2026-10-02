"""Tests for shared_utils.access. No network: a local RSA key stands in for Cloudflare's."""

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from shared_utils import access
from shared_utils.access import AccessError, token_from_request, verify_access_token

TEAM = "testteam"
AUD = "test-aud"
ISSUER = "https://testteam.cloudflareaccess.com"


def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


SIGNING_KEY = _key()


class _FakeJWKClient:
    def __init__(self, public_key):
        self.public_key = public_key

    def get_signing_key_from_jwt(self, token):
        return jwt.PyJWK.from_dict(jwt.algorithms.RSAAlgorithm.to_jwk(self.public_key, as_dict=True))


@pytest.fixture(autouse=True)
def fake_keys(monkeypatch):
    monkeypatch.setattr(access, "_jwks_client", lambda team: _FakeJWKClient(SIGNING_KEY.public_key()))


def _token(key=SIGNING_KEY, **overrides):
    claims = {"email": "gm@example.com", "aud": [AUD], "iss": ISSUER, "exp": int(time.time()) + 300}
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256")


def test_valid_token_returns_claims():
    assert verify_access_token(_token(), team=TEAM, aud=AUD)["email"] == "gm@example.com"


@pytest.mark.parametrize("overrides", [
    {"aud": ["another-app"]},
    {"iss": "https://otherteam.cloudflareaccess.com"},
    {"exp": int(time.time()) - 60},
])
def test_wrong_audience_issuer_or_expired_is_rejected(overrides):
    with pytest.raises(AccessError, match="Cloudflare Access token rejected"):
        verify_access_token(_token(**overrides), team=TEAM, aud=AUD)


def test_token_signed_by_another_key_is_rejected():
    with pytest.raises(AccessError):
        verify_access_token(_token(key=_key()), team=TEAM, aud=AUD)


def test_garbage_is_rejected():
    with pytest.raises(AccessError):
        verify_access_token("eyJhbGciOiJSUzI1NiJ9.e30.notasignature", team=TEAM, aud=AUD)


def test_token_from_request_prefers_header_then_cookie():
    assert token_from_request({"Cf-Access-Jwt-Assertion": "h"}, {"CF_Authorization": "c"}) == "h"
    assert token_from_request({}, {"CF_Authorization": "c"}) == "c"
    assert token_from_request({}, {}) is None
