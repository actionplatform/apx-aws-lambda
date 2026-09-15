"""`aws/lambda`: a SAM stack per stage. Deploy = `sam build` + `sam deploy --config-env <stage>`; rollback = CloudFormation rollback to the previous stack state; diagnose = stack status and the HTTP API url.

Credentials, in order: `proxy_url` + `app` under `[deploy]` — the deploy proxy in the account exchanges a platform token for the app's deploy-role credentials; `role_arn` — the target assumes that role with the same token (`sts assume-role-with-web-identity`); neither — the AWS CLI's own chain (profile, SSO, instance role). No access key anywhere."""

from __future__ import annotations

import os
import tomllib

from action_platform.abc import DeployTarget
from action_platform.core.context import Context, DeployResult, Diagnosis
from action_platform.core.exception import ActionPlatformError, DeployError
from action_platform.remote.client import Remote

from apx_aws_lambda import shell
from apx_aws_lambda.proxy import ProxyClient


class LambdaTarget(DeployTarget):
    name = "aws/lambda"

    def __init__(
        self,
        region: str | None = None,
        config: str = "samconfig.toml",
        role_arn: str | None = None,
        session_name: str = "action-platform",
        proxy_url: str | None = None,
        app: str | None = None,
        **_: object,
    ) -> None:
        self.region = (
            region
            or os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
        )
        self.config = config
        self.role_arn = role_arn or os.environ.get("AWS_ROLE_ARN")
        self.session_name = session_name
        self.proxy = (
            ProxyClient(proxy_url, app or os.environ.get("AP_APP", ""))
            if proxy_url
            else None
        )
        self._env: dict[str, str] | None = None

    def env(self, ctx: Context) -> dict[str, str] | None:
        """The environment `aws` and `sam` run with: the proxy's credentials, else temporary credentials from `role_arn`, else the caller's own."""
        if self.proxy is None and not self.role_arn:
            return None

        if self._env is None:
            granted = (
                self.proxy.env(ctx)
                if self.proxy is not None
                else assume_role(
                    ctx, self.role_arn or "", self.session_name, self.region
                )
            )
            self._env = {**os.environ, **granted}

        return self._env

    def _stage(self, ctx: Context) -> str:
        return "prod" if ctx.stage == "prod" else "default"

    def _samconfig(self, ctx: Context) -> dict:
        path = ctx.repo_root / self.config

        if not path.exists():
            raise DeployError(
                f"{self.config} not found: apply the aws/lambda overlay first"
            )

        return tomllib.loads(path.read_text())

    def _stack(self, ctx: Context) -> str:
        params = (
            self._samconfig(ctx)
            .get(self._stage(ctx), {})
            .get("deploy", {})
            .get("parameters", {})
        )
        stack = params.get("stack_name")

        if not stack:
            raise DeployError(f"{self.config} has no stack_name for {self._stage(ctx)}")

        return stack

    def _overrides(self, ctx: Context, env: dict[str, str] | None) -> list[str]:
        """`--parameter-overrides`: the stage's own from samconfig.toml, plus the execution role the proxy granted — the CLI flag replaces the file's, so both go together."""
        role = (env or {}).get("AP_EXECUTION_ROLE")

        if not role:
            return []

        params = (
            self._samconfig(ctx)
            .get(self._stage(ctx), {})
            .get("deploy", {})
            .get("parameters", {})
        )
        own = params.get("parameter_overrides") or ""

        return ["--parameter-overrides", f"{own} ExecutionRoleArn={role}".strip()]

    def _region(self, ctx: Context) -> str | None:
        params = (
            self._samconfig(ctx)
            .get(self._stage(ctx), {})
            .get("deploy", {})
            .get("parameters", {})
        )

        return self.region or params.get("region")

    def preflight(self, ctx: Context) -> None:
        shell.require("sam", "pip install aws-sam-cli")
        shell.require("aws", "https://aws.amazon.com/cli/")

        if not (ctx.repo_root / "template.yaml").exists():
            raise DeployError(
                "template.yaml not found: apply the aws/lambda overlay first"
            )

        stack = self._stack(ctx)
        env = self.env(ctx)
        prefix = (env or {}).get("AP_STACK_PREFIX")

        if prefix and not stack.startswith(prefix):
            raise DeployError(
                f"stack_name {stack!r} must start with {prefix!r}: the proxy grants deploy on that prefix only"
            )

        shell.aws("sts", "get-caller-identity", region=self._region(ctx), env=env)
        shell.run(
            [*shell.require("sam", ""), "validate", "--lint"],
            cwd=ctx.repo_root,
            env=self.env(ctx),
        )

    def deploy(self, ctx: Context) -> DeployResult:
        sam = shell.require("sam", "pip install aws-sam-cli")
        stage = self._stage(ctx)
        env = self.env(ctx)
        shell.run([*sam, "build"], cwd=ctx.repo_root, env=env)
        args = [
            *sam,
            "deploy",
            "--no-confirm-changeset",
            "--no-fail-on-empty-changeset",
        ]

        if stage != "default":
            args += ["--config-env", stage]

        args += self._overrides(ctx, env)
        shell.run(args, cwd=ctx.repo_root, env=env)
        url = self._url(ctx)

        return DeployResult(
            ok=True, target=self.name, version=ctx.next_version, url=url
        )

    def rollback(self, ctx: Context, to_version: str | None = None) -> None:
        if to_version:
            raise DeployError(
                "aws/lambda rolls back to the previous stack state only; "
                "to reach a version, check it out and deploy"
            )

        shell.aws(
            "cloudformation",
            "rollback-stack",
            "--stack-name",
            self._stack(ctx),
            region=self._region(ctx),
            env=self.env(ctx),
        )

    def diagnose(self, ctx: Context) -> Diagnosis:
        stack = self._stack(ctx)

        try:
            data = shell.aws(
                "cloudformation",
                "describe-stacks",
                "--stack-name",
                stack,
                region=self._region(ctx),
                env=self.env(ctx),
            )
        except DeployError as e:
            return Diagnosis(
                ok=False, target=self.name, status="missing", details={"error": str(e)}
            )

        rows = data.get("Stacks") or []
        status = rows[0].get("StackStatus", "") if rows else "missing"
        outputs = (
            {o["OutputKey"]: o["OutputValue"] for o in (rows[0].get("Outputs") or [])}
            if rows
            else {}
        )
        url = (
            outputs.get("ApiUrl")
            or outputs.get("HttpApiUrl")
            or next((v for k, v in outputs.items() if "url" in k.lower()), None)
        )

        return Diagnosis(
            ok=status.endswith("_COMPLETE") and not status.startswith("ROLLBACK"),
            target=self.name,
            status=status,
            url=url,
            details={"stack": stack, **{k: str(v) for k, v in outputs.items()}},
        )

    def delete(self, ctx: Context) -> None:
        sam = shell.require("sam", "pip install aws-sam-cli")
        args = [*sam, "delete", "--no-prompts", "--stack-name", self._stack(ctx)]
        region = self._region(ctx)

        if region:
            args += ["--region", region]

        shell.run(args, cwd=ctx.repo_root, env=self.env(ctx))

    def _url(self, ctx: Context) -> str | None:
        try:
            return self.diagnose(ctx).url
        except DeployError:
            return None


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
