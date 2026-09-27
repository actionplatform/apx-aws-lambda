"""Which AWS identity a deploy acts as, in order: a role the repository names (`[deploy] role_arn`), the organization's connected account, the AWS CLI's own chain. Every one without an access key: the first two exchange the platform's OIDC token at STS."""

from __future__ import annotations

import os

from action_platform.core.context import Context
from action_platform.core.exception import ActionPlatformError, DeployError
from action_platform.remote.client import Remote

from apx_aws_lambda import shell
from apx_aws_lambda.abc import Access, Credentials
from apx_aws_lambda.spec import Spec


class CliChain(Credentials):
    """The caller's own: `aws sso login`, a profile, an instance role."""

    def access(self, spec: Spec) -> Access:
        return Access(env=None)


class OwnRole(Credentials):
    """`[deploy] role_arn`, assumed with the platform's token."""

    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx

    def access(self, spec: Spec) -> Access:
        granted = assume_role(
            self.ctx,
            spec.options.role_arn or "",
            spec.options.session_name,
            spec.options.region,
        )

        return Access(env={**os.environ, **granted})


class ConnectedAccount(Credentials):
    """The connect stack's deploy role, assumed with the platform's token for this app — whose session tag is the app's prefix."""

    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx

    def access(self, spec: Spec) -> Access:
        assert spec.connected is not None
        granted = assume_role(
            self.ctx,
            spec.connected.role,
            spec.options.session_name,
            spec.options.region,
        )

        return Access(env={**os.environ, **granted}, deploy_role=spec.connected.role)


def credentials_for(ctx: Context, spec: Spec) -> Credentials:
    if spec.connected:
        return ConnectedAccount(ctx)

    if spec.options.role_arn:
        return OwnRole(ctx)

    return CliChain()


def assume_role(
    ctx: Context, role_arn: str, session_name: str, region: str | None
) -> dict[str, str]:
    """Temporary credentials for `role_arn` from an OIDC token: the platform's for a deploy it runs, the platform the CLI is logged in to otherwise."""
    token = ctx.identity_token("sts.amazonaws.com")

    if token is None:
        try:
            token = Remote.from_credentials().identity_token("sts.amazonaws.com")[
                "token"
            ]
        except ActionPlatformError as e:
            raise DeployError(
                f"role_arn is set but nothing can sign an identity token here: {e}. "
                "Deploy from the platform, or log in with `action-platform login`."
            ) from e

    data = shell.aws(
        "sts",
        "assume-role-with-web-identity",
        "--role-arn",
        role_arn,
        "--role-session-name",
        session_name,
        "--web-identity-token",
        token,
        "--duration-seconds",
        "3600",
        region=region,
        env={
            k: v
            for k, v in os.environ.items()
            if not k.startswith("AWS_ACCESS") and k != "AWS_SESSION_TOKEN"
        },
    )
    creds = data.get("Credentials") or {}

    if not creds.get("AccessKeyId"):
        raise DeployError(
            f"assume-role-with-web-identity gave no credentials for {role_arn}"
        )

    return {
        "AWS_ACCESS_KEY_ID": creds["AccessKeyId"],
        "AWS_SECRET_ACCESS_KEY": creds["SecretAccessKey"],
        "AWS_SESSION_TOKEN": creds["SessionToken"],
    }
