"""What the function is told about its account and its platform — environment at cold start, constants beside it."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

SAM_BUCKET = "aws-sam-cli-managed-default"
POLICY_VERSION = 3
PUBLIC_LAYERS = [("753240598075", "LambdaAdapterLayer*")]
PATH = "/action-platform/"
MIN_DURATION = 900
MAX_DURATION = 3600
ASSUME_ATTEMPTS = 6
ASSUME_BACKOFF = 2.0
LAMBDA_BASIC_EXECUTION = (
    "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
)


@dataclass(frozen=True)
class Settings:
    issuer: str
    organization: str
    table: str
    boundary_arn: str
    account_id: str
    version: str
    default_region: str = field(default="us-east-1")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            issuer=os.environ.get("ISSUER_URL", "").rstrip("/"),
            organization=os.environ.get("ORGANIZATION", ""),
            table=os.environ.get("TABLE", ""),
            boundary_arn=os.environ.get("BOUNDARY_ARN", ""),
            account_id=os.environ.get("ACCOUNT_ID", ""),
            version=os.environ.get("PROXY_VERSION", "0.0.0"),
            default_region=os.environ.get("AWS_REGION", "us-east-1"),
        )

    @property
    def jwks_url(self) -> str:
        return f"{self.issuer}/.well-known/jwks.json"
