"""One app of the organization as the proxy names things for it: its key, its stack prefix, its two roles, and the policy its deploy role carries."""

from __future__ import annotations

import hashlib
import re

from errors import Refused
from settings import PATH, PUBLIC_LAYERS, SAM_BUCKET, Settings

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
ROLE_NAME_MAX = 64


class App:
    def __init__(self, org: str, project: str, name: str, settings: Settings) -> None:
        for part in (org, project, name):
            if not SLUG.match(part):
                raise Refused(400, f"bad slug {part!r}")

        if org != settings.organization:
            raise Refused(
                403, f"this proxy serves organization {settings.organization!r}"
            )

        self.org, self.project, self.name = org, project, name
        self.settings = settings

    @property
    def key(self) -> str:
        return f"{self.org}/{self.project}/{self.name}"

    @property
    def subject(self) -> str:
        return f"org:{self.org}:project:{self.project}:app:{self.name}"

    @property
    def prefix(self) -> str:
        return f"ap-{self.org}-{self.project}-{self.name}"

    def role_name(self, kind: str) -> str:
        name = f"ap-{kind}-{self.org}-{self.project}-{self.name}"

        if len(name) <= ROLE_NAME_MAX:
            return name

        digest = hashlib.sha256(name.encode()).hexdigest()[:8]

        return f"{name[: ROLE_NAME_MAX - 9]}-{digest}"

    def role_arn(self, kind: str) -> str:
        return (
            f"arn:aws:iam::{self.settings.account_id}:role{PATH}{self.role_name(kind)}"
        )

    def tags(self) -> list[dict[str, str]]:
        return [
            {"Key": "action-platform:app", "Value": self.key},
            {"Key": "action-platform:prefix", "Value": self.prefix},
            {"Key": "action-platform:managed", "Value": "true"},
        ]

    def deploy_policy(self, region: str) -> dict:
        return DeployPolicy(self, region).document()

    def view(self, row: dict) -> dict:
        return {
            "app": self.key,
            "subject": self.subject,
            "region": row.get("region"),
            "stack_prefix": self.prefix,
            "deploy_role": self.role_arn("deploy"),
            "execution_role": self.role_arn("exec"),
            "subjects": list(row.get("subjects") or []),
        }


class DeployPolicy:
    """What the deploy role may do: CloudFormation on the app's stacks, Lambda, API Gateway and logs under its prefix, the public layers, PassRole on the execution role, simulating itself, SAM's bucket."""

    def __init__(self, app: App, region: str) -> None:
        self.app = app
        self.region = region
        self.account = app.settings.account_id

    def _allow(self, actions: list[str], resources: list[str] | str) -> dict:
        return {"Effect": "Allow", "Action": actions, "Resource": resources}

    def document(self) -> dict:
        region, account, prefix = self.region, self.account, self.app.prefix

        return {
            "Version": "2012-10-17",
            "Statement": [
                self._allow(
                    ["cloudformation:*"],
                    [f"arn:aws:cloudformation:{region}:{account}:stack/{prefix}*"],
                ),
                self._allow(
                    [
                        "cloudformation:ListStacks",
                        "cloudformation:ValidateTemplate",
                        "cloudformation:GetTemplateSummary",
                        "cloudformation:CreateChangeSet",
                    ],
                    "*",
                ),
                self._allow(
                    ["lambda:*"],
                    [
                        f"arn:aws:lambda:{region}:{account}:function:{prefix}*",
                        f"arn:aws:lambda:{region}:{account}:layer:{prefix}*",
                    ],
                ),
                self._allow(
                    ["lambda:GetLayerVersion"],
                    [
                        f"arn:aws:lambda:{region}:{owner}:layer:{layer}:*"
                        for owner, layer in PUBLIC_LAYERS
                    ],
                ),
                self._allow(["apigateway:*"], [f"arn:aws:apigateway:{region}::/*"]),
                self._allow(
                    ["logs:*"],
                    [
                        f"arn:aws:logs:{region}:{account}:log-group:/aws/lambda/{prefix}*"
                    ],
                ),
                self._allow(
                    ["iam:PassRole", "iam:GetRole"], [self.app.role_arn("exec")]
                ),
                self._allow(
                    ["iam:GetRole", "iam:SimulatePrincipalPolicy"],
                    [self.app.role_arn("deploy")],
                ),
                self._allow(
                    ["cloudformation:*"],
                    [f"arn:aws:cloudformation:{region}:{account}:stack/{SAM_BUCKET}*"],
                ),
                self._allow(
                    ["s3:*"],
                    [f"arn:aws:s3:::{SAM_BUCKET}-*", f"arn:aws:s3:::{SAM_BUCKET}-*/*"],
                ),
            ],
        }
