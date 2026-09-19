"""Who is calling: the platform token verified against the issuer's keys, scoped to this proxy and its organization."""

from __future__ import annotations

from errors import Refused
from oidc import Keys
from oidc import verify as verify_token
from settings import Settings


class Auth:
    def __init__(self, keys: Keys, settings: Settings) -> None:
        self.keys = keys
        self.settings = settings

    def claims(self, headers: dict, own_url: str) -> dict:
        auth = headers.get("authorization") or headers.get("Authorization") or ""

        if not auth.lower().startswith("bearer "):
            raise Refused(401, "bearer token required")

        token = auth[7:].strip()
        claims = verify_token(
            token, self.keys, self.settings.issuer, [own_url, own_url + "/"]
        )

        if claims.get("organization") != self.settings.organization:
            raise Refused(
                403, f"token is for organization {claims.get('organization')!r}"
            )

        return claims

    @staticmethod
    def admin(claims: dict) -> None:
        if "org.manage" not in (claims.get("scopes") or []):
            raise Refused(403, "org.manage is required")

    @staticmethod
    def actor(claims: dict) -> str:
        return str(claims.get("actor") or claims["sub"])
