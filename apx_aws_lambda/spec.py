"""What one call is about, resolved once from the target's options, the context and `samconfig.toml`: the scope, the stack, the region, and — for a connected account — the app's prefix, deploy role and boundary. Nothing here talks to AWS."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from action_platform.core.context import Context
from action_platform.core.exception import DeployError

BOUNDARY = "policy/action-platform/ActionPlatformAppBoundary"
PREFIX_TAG = "action-platform:prefix"


@dataclass(frozen=True)
class Options:
    """What `[deploy]` in platform.toml passes to the target."""

    region: str | None = None
    config: str = "samconfig.toml"
    role_arn: str | None = None
    session_name: str = "action-platform"
    app: str | None = None
    health: str = "/health"


@dataclass(frozen=True)
class Connected:
    """The organization's connected account: the deploy role its connect stack made, and the app's prefix every resource is scoped by."""

    role: str
    prefix: str

    @property
    def account(self) -> str:
        return self.role.split(":")[4] if self.role.count(":") >= 5 else ""

    @property
    def boundary(self) -> str:
        return f"arn:aws:iam::{self.account}:{BOUNDARY}"

    @property
    def role_path(self) -> str:
        return role_path(self.prefix)


@dataclass(frozen=True)
class Spec:
    root: Path
    scope: str
    version: str
    options: Options
    connected: Connected | None
    samconfig: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def of(cls, ctx: Context, options: Options) -> Spec:
        path = ctx.repo_root / options.config
        samconfig = tomllib.loads(path.read_text()) if path.exists() else {}

        return cls(
            root=ctx.repo_root,
            scope=ctx.stage or "",
            version=ctx.next_version,
            options=options,
            connected=_connected(ctx, options),
            samconfig=samconfig,
        )

    @property
    def config_env(self) -> str:
        return "prod" if self.scope == "prod" else "default"

    @property
    def params(self) -> dict[str, Any]:
        if not (self.root / self.options.config).exists():
            raise DeployError(
                f"{self.options.config} not found: apply the aws/lambda overlay first"
            )

        return (
            self.samconfig.get(self.config_env, {})
            .get("deploy", {})
            .get("parameters", {})
        )

    @property
    def stack(self) -> str:
        """`<prefix>-<scope>` for a connected account — the repository's samconfig.toml never has to know the organization — else samconfig.toml's `stack_name`."""
        if self.connected:
            return f"{self.connected.prefix}-{self.scope or 'dev'}"

        stack = self.params.get("stack_name")

        if not stack:
            raise DeployError(
                f"{self.options.config} has no stack_name for {self.config_env}"
            )

        return stack

    @property
    def region(self) -> str | None:
        return self.options.region or self.params.get("region")

    @property
    def overrides(self) -> list[str]:
        """`--parameter-overrides`: the stage's own from samconfig.toml, then Stage set to the scope's name and the boundary of a connected account — the CLI flag replaces the file's, so they all go together."""
        own = [
            item
            for item in str(self.params.get("parameter_overrides") or "").split()
            if not item.startswith(("Stage=", "PermissionsBoundaryArn=", "RolePath="))
        ]
        own.append(f"Stage={self.scope}")

        if self.connected:
            own.append(f"PermissionsBoundaryArn={self.connected.boundary}")
            own.append(f"RolePath={self.connected.role_path}")

        return ["--parameter-overrides", " ".join(own)]

    @property
    def tags(self) -> list[str]:
        """`--tags` for a connected account: the stage's own from samconfig.toml plus the app's prefix, which the stack hands to the execution role it creates — the tag the boundary scopes that role by."""
        if not self.connected:
            return []

        own = [
            item
            for item in str(self.params.get("tags") or "").split()
            if not item.startswith(f"{PREFIX_TAG}=")
        ]
        own.append(f"{PREFIX_TAG}={self.connected.prefix}")

        return ["--tags", " ".join(own)]

    @property
    def template(self) -> Path:
        return self.root / "template.yaml"

    @property
    def takes_boundary(self) -> bool:
        text = self.template.read_text(errors="replace")

        return "PermissionsBoundaryArn" in text and "RolePath" in text


def role_path(prefix: str) -> str:
    """Where a connected account keeps an app's execution roles: CloudFormation cuts a generated role name to 64 characters, which can drop the prefix from it, so the app's roles are told apart by their IAM path instead."""
    return f"/action-platform/{prefix}/"


def _connected(ctx: Context, options: Options) -> Connected | None:
    """The organization's connected account (`AP_AWS_LAMBDA_ROLE_ARN`: an account ID or the role's ARN) and the app's prefix from `AP_APP` — unless `[deploy]` names a role of its own."""
    if options.role_arn:
        return None

    role = ctx.env.get("AP_AWS_LAMBDA_ROLE_ARN") or os.environ.get(
        "AP_AWS_LAMBDA_ROLE_ARN"
    )

    if not role:
        return None

    if re.fullmatch(r"\d{12}", role.strip()):
        role = f"arn:aws:iam::{role.strip()}:role/action-platform/ActionPlatformDeploy"

    app = options.app or ctx.env.get("AP_APP") or os.environ.get("AP_APP", "")
    parts = app.strip("/").split("/")

    if len(parts) != 3 or not all(parts):
        raise DeployError(
            f"the organization's AWS account is connected, but the app is not known as <org>/<project>/<app> (got {app!r}): deploy from the platform"
        )

    return Connected(role=role, prefix="ap-" + "-".join(parts))
