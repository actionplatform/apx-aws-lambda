"""RS256 by hand against a key `cryptography` signs with — the platform's own algorithm."""

import base64
import json
import sys
import time
import unittest
from pathlib import Path

try:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from oidc import Keys, TokenError, verify  # noqa: E402


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


@unittest.skipUnless(HAS_CRYPTO, "cryptography is not installed")
class VerifyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = cls.private.public_key().public_numbers()
        cls.keys = Keys("https://platform.test/.well-known/jwks.json")
        cls.keys.cached = {"k1": (numbers.n, numbers.e)}

    def token(self, kid="k1", **claims):
        now = int(time.time())
        payload = {
            "iss": "https://platform.test",
            "aud": "https://p.test",
            "iat": now,
            "nbf": now - 5,
            "exp": now + 300,
            "sub": "org:acme",
            **claims,
        }
        head = b64(json.dumps({"alg": "RS256", "typ": "JWT", "kid": kid}).encode())
        body = b64(json.dumps(payload).encode())
        signature = self.private.sign(
            f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256()
        )

        return f"{head}.{body}.{b64(signature)}"

    def test_a_good_token_verifies(self):
        claims = verify(
            self.token(), self.keys, "https://platform.test", ["https://p.test"]
        )

        self.assertEqual(claims["sub"], "org:acme")

    def test_a_tampered_payload_fails(self):
        head, body, sig = self.token().split(".")
        forged = b64(json.dumps({"sub": "org:evil", "exp": 9999999999}).encode())

        with self.assertRaises(TokenError):
            verify(
                f"{head}.{forged}.{sig}",
                self.keys,
                "https://platform.test",
                ["https://p.test"],
            )

    def test_wrong_audience_issuer_or_expiry_fail(self):
        for token, why in [
            (self.token(aud="https://other"), "audience"),
            (self.token(iss="https://other"), "issuer"),
            (self.token(exp=int(time.time()) - 60), "expired"),
        ]:
            with self.assertRaises(TokenError) as caught:
                verify(token, self.keys, "https://platform.test", ["https://p.test"])

            self.assertIn(why, str(caught.exception))

    def test_unknown_kid_refetches_then_fails(self):
        keys = Keys("https://platform.test/.well-known/jwks.json")
        keys.fetch = lambda: None

        with self.assertRaises(TokenError) as caught:
            verify(
                self.token(kid="k9"), keys, "https://platform.test", ["https://p.test"]
            )

        self.assertIn("unknown key", str(caught.exception))
