"""RS256 verification with nothing but the standard library: the issuer's JWKS, `pow(signature, e, n)`, PKCS#1 v1.5 padding around the SHA-256 DigestInfo — so the function needs no compiled dependency and `sam build` needs no pip."""

from __future__ import annotations

import base64
import hashlib
import json
import time
import urllib.request

SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


class TokenError(Exception):
    pass


def b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class Keys:
    def __init__(self, jwks_url: str, timeout: float = 5.0) -> None:
        self.url = jwks_url
        self.timeout = timeout
        self.cached: dict[str, tuple[int, int]] = {}

    def fetch(self) -> None:
        with urllib.request.urlopen(self.url, timeout=self.timeout) as response:
            data = json.loads(response.read())

        self.cached = {
            k["kid"]: (
                int.from_bytes(b64d(k["n"]), "big"),
                int.from_bytes(b64d(k["e"]), "big"),
            )
            for k in data.get("keys") or []
            if k.get("kty") == "RSA" and k.get("kid")
        }

    def get(self, kid: str) -> tuple[int, int]:
        if kid not in self.cached:
            self.fetch()

        if kid not in self.cached:
            raise TokenError(f"unknown key {kid!r}")

        return self.cached[kid]


def verify(
    token: str, keys: Keys, issuer: str, audiences: list[str], leeway: int = 10
) -> dict:
    try:
        head, payload, signature = token.split(".")
        header = json.loads(b64d(head))
        claims = json.loads(b64d(payload))
    except (ValueError, TypeError) as e:
        raise TokenError(f"malformed token: {e}") from e

    if header.get("alg") != "RS256":
        raise TokenError(f"alg {header.get('alg')!r} is not RS256")

    n, e = keys.get(str(header.get("kid")))

    if not rsa_ok(f"{head}.{payload}".encode(), b64d(signature), n, e):
        raise TokenError("bad signature")

    now = int(time.time())

    if claims.get("iss") != issuer:
        raise TokenError(f"issuer {claims.get('iss')!r} is not {issuer!r}")

    aud = claims.get("aud")
    aud = aud if isinstance(aud, list) else [aud]

    if not any(a in audiences for a in aud):
        raise TokenError(f"audience {aud!r} is not this proxy")

    if int(claims.get("exp", 0)) + leeway < now:
        raise TokenError("token expired")

    if int(claims.get("nbf", 0)) - leeway > now:
        raise TokenError("token not yet valid")

    return claims


def rsa_ok(message: bytes, signature: bytes, n: int, e: int) -> bool:
    size = (n.bit_length() + 7) // 8

    if len(signature) != size:
        return False

    decrypted = pow(int.from_bytes(signature, "big"), e, n).to_bytes(size, "big")
    digest = hashlib.sha256(message).digest()
    expected = (
        b"\x00\x01"
        + b"\xff" * (size - 3 - len(SHA256_PREFIX) - len(digest))
        + b"\x00"
        + SHA256_PREFIX
        + digest
    )

    return decrypted == expected
