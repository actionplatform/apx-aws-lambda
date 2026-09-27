"""`aws/lambda`: a SAM stack per stage. Deploy = `sam build` + `sam deploy --config-env <stage>`; rollback = CloudFormation rollback to the previous stack state; diagnose = stack status and the HTTP API url.

Credentials, in order: `role_arn` under `[deploy]` — the target assumes that role with a platform token (`sts assume-role-with-web-identity`); the organization's connected account (`AP_AWS_LAMBDA_ROLE_ARN` with `AP_APP`) — the deploy role the connect stack made, scoped to the app by the token's session tag; neither — the AWS CLI's own chain (profile, SSO, instance role). No access key anywhere."""

from __future__ import annotations

import os
from dataclasses import dataclass

from action_platform.abc import DeployTarget
from action_platform.core.context import Check, Context, DeployResult, Diagnosis
from action_platform.core.exception import DeployError

from apx_aws_lambda import shell
from apx_aws_lambda.abc import Access, Credentials, Sam, Stack
from apx_aws_lambda.checks import (
    BoundaryCheck,
    CredentialsCheck,
    PermissionsCheck,
    StackNameCheck,
    StackStateCheck,
    TemplateCheck,
    TemplateValidCheck,
    ToolCheck,
)
from apx_aws_lambda.credentials import credentials_for
from apx_aws_lambda.sam import HINT, SamCli
from apx_aws_lambda.spec import Options, Spec
from apx_aws_lambda.stack import CloudFormationStack, outputs_of, url_of


@dataclass(frozen=True)
class Parts:
    """Every responsibility wired to one identity."""

    access: Access
    stack: Stack
    sam: Sam


class LambdaTarget(DeployTarget):
    name = "aws/lambda"

    def __init__(
        self,
        region: str | None = None,
        config: str = "samconfig.toml",
        role_arn: str | None = None,
        session_name: str = "action-platform",
        app: str | None = None,
        **_: object,
    ) -> None:
        self.options = Options(
            region=region
            or os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION"),
            config=config,
            role_arn=role_arn or os.environ.get("AWS_ROLE_ARN"),
            session_name=session_name,
            app=app,
        )

    def spec(self, ctx: Context) -> Spec:
        return Spec.of(ctx, self.options)

    def credentials(self, ctx: Context, spec: Spec) -> Credentials:
        return credentials_for(ctx, spec)

    def parts(self, spec: Spec, access: Access) -> Parts:
        """The implementation of every responsibility for one identity; override to swap one."""
        return Parts(
            access=access,
            stack=CloudFormationStack(spec, access),
            sam=SamCli(spec, access),
        )

    def env(self, ctx: Context) -> dict[str, str] | None:
        """The environment `aws` and `sam` run with: temporary credentials from `role_arn` or the connected account's deploy role, else the caller's own."""
        spec = self.spec(ctx)

        return self.credentials(ctx, spec).access(spec).env

    def preflight(self, ctx: Context) -> None:
        shell.require("sam", HINT)
        shell.require("aws", "https://aws.amazon.com/cli/")
        spec = self.spec(ctx)

        if not spec.template.exists():
            raise DeployError(
                "template.yaml not found: apply the aws/lambda overlay first"
            )

        spec.stack
        parts = self.parts(spec, self.credentials(ctx, spec).access(spec))
        shell.aws(
            "sts", "get-caller-identity", region=spec.region, env=parts.access.env
        )
        parts.sam.validate()

    def readiness(self, ctx: Context) -> list[Check]:
        spec = self.spec(ctx)
        checks = [
            ToolCheck("sam", HINT).run(spec),
            ToolCheck("aws", "https://aws.amazon.com/cli/").run(spec),
        ]

        for gate in (TemplateCheck(), StackNameCheck()):
            checks.append(gate.run(spec))

            if not checks[-1].ok:
                return checks

        try:
            access = self.credentials(ctx, spec).access(spec)
        except DeployError as e:
            checks.append(
                Check(
                    CredentialsCheck.id,
                    False,
                    str(e),
                    fix="connect the AWS account in the AWS Lambda plugin",
                )
            )

            return checks

        credentials = CredentialsCheck(access)
        checks.append(credentials.run(spec))

        if not checks[-1].ok:
            return checks

        parts = self.parts(spec, access)

        if spec.connected:
            checks.append(BoundaryCheck().run(spec))

        checks.append(StackStateCheck(parts.stack).run(spec))
        permissions = PermissionsCheck(access, credentials).run(spec)

        if permissions is not None:
            checks.append(permissions)

        checks.append(TemplateValidCheck(parts.sam).run(spec))

        return checks

    def deploy(self, ctx: Context) -> DeployResult:
        shell.require("sam", HINT)
        spec = self.spec(ctx)
        parts = self.parts(spec, self.credentials(ctx, spec).access(spec))

        if spec.connected and not spec.takes_boundary:
            raise DeployError(
                "template.yaml lacks the PermissionsBoundaryArn or RolePath parameter, so the execution role "
                "would land outside what a connected account may create: apply the aws/lambda overlay again "
                "(Configuration → Deploy target, or action-platform cloud set aws/lambda)"
            )

        parts.sam.build()
        parts.stack.clear_failed_creation()
        parts.sam.deploy()

        return DeployResult(
            ok=True,
            target=self.name,
            version=spec.version,
            url=url_of(outputs_of(parts.stack.describe())),
        )

    def rollback(self, ctx: Context, to_version: str | None = None) -> None:
        if to_version:
            raise DeployError(
                "aws/lambda rolls back to the previous stack state only; "
                "to reach a version, check it out and deploy"
            )

        spec = self.spec(ctx)
        self.parts(spec, self.credentials(ctx, spec).access(spec)).stack.rollback()

    def diagnose(self, ctx: Context) -> Diagnosis:
        spec = self.spec(ctx)
        stack = spec.stack
        row = self.parts(
            spec, self.credentials(ctx, spec).access(spec)
        ).stack.describe()

        if row is None:
            return Diagnosis(
                ok=False, target=self.name, status="missing", details={"stack": stack}
            )

        status = row.get("StackStatus", "")
        outputs = outputs_of(row)

        return Diagnosis(
            ok=status.endswith("_COMPLETE") and not status.startswith("ROLLBACK"),
            target=self.name,
            status=status,
            url=url_of(outputs),
            details={"stack": stack, **{k: str(v) for k, v in outputs.items()}},
        )

    def delete(self, ctx: Context) -> None:
        """The stage's stack goes, and with it the execution role SAM created."""
        spec = self.spec(ctx)
        parts = self.parts(spec, self.credentials(ctx, spec).access(spec))

        if parts.stack.describe() is None:
            return

        parts.sam.delete()
