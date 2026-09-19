"""`aws/lambda`: a SAM stack per stage. Deploy = `sam build` + `sam deploy --config-env <stage>`; rollback = CloudFormation rollback to the previous stack state; diagnose = stack status and the HTTP API url.

Credentials, in order: `proxy_url` + `app` under `[deploy]` — the deploy proxy in the account exchanges a platform token for the app's deploy-role credentials; `role_arn` — the target assumes that role with the same token (`sts assume-role-with-web-identity`); neither — the AWS CLI's own chain (profile, SSO, instance role). No access key anywhere."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import replace

from action_platform.abc import DeployTarget
from action_platform.core.context import Check, Context, DeployResult, Diagnosis
from action_platform.core.exception import ActionPlatformError, DeployError
from action_platform.logging import logger
from action_platform.remote.client import Remote

from apx_aws_lambda import shell
from apx_aws_lambda.proxy import ProxyClient, ProxyRefused


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
        self.proxy_url = proxy_url
        self.app = app
        self._env: dict[str, str] | None = None

    def proxy(self, ctx: Context) -> ProxyClient | None:
        """The deploy proxy, when one is named: `[deploy] proxy_url` and `app` in platform.toml, else what the platform set for the organization (`AP_AWS_LAMBDA_PROXY_URL`, `AP_APP` in `ctx.env`), else the process environment."""
        url = (
            self.proxy_url
            or ctx.env.get("AP_AWS_LAMBDA_PROXY_URL")
            or os.environ.get("AP_AWS_LAMBDA_PROXY_URL")
        )

        if not url:
            return None

        app = self.app or ctx.env.get("AP_APP") or os.environ.get("AP_APP", "")

        return ProxyClient(url, app)

    def env(self, ctx: Context) -> dict[str, str] | None:
        """The environment `aws` and `sam` run with: the proxy's credentials, else temporary credentials from `role_arn`, else the caller's own."""
        if self._env is not None:
            return self._env

        proxy = self.proxy(ctx)

        if proxy is None and not self.role_arn:
            return None

        granted = (
            proxy.env(ctx, self.region)
            if proxy is not None
            else assume_role(ctx, self.role_arn or "", self.session_name, self.region)
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
        """The stack: `<prefix>-<stage>` when the proxy granted a prefix — the repository's samconfig.toml never has to know the organization — else samconfig.toml's `stack_name`."""
        prefix = (self.env(ctx) or {}).get("AP_STACK_PREFIX")

        if prefix:
            return f"{prefix}-{'prod' if ctx.stage == 'prod' else 'dev'}"

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
        """`--parameter-overrides`: the stage's own from samconfig.toml, then Stage set to the scope's name and the execution role the proxy granted — the CLI flag replaces the file's, so they all go together."""
        params = (
            self._samconfig(ctx)
            .get(self._stage(ctx), {})
            .get("deploy", {})
            .get("parameters", {})
        )
        own = [
            item
            for item in str(params.get("parameter_overrides") or "").split()
            if not item.startswith(("Stage=", "ExecutionRoleArn="))
        ]
        own.append(f"Stage={ctx.stage}")
        role = (env or {}).get("AP_EXECUTION_ROLE")

        if role:
            own.append(f"ExecutionRoleArn={role}")

        return ["--parameter-overrides", " ".join(own)]

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

        self._stack(ctx)
        env = self.env(ctx)
        shell.aws("sts", "get-caller-identity", region=self._region(ctx), env=env)
        shell.run(
            [*shell.require("sam", ""), "validate", "--lint"],
            cwd=ctx.repo_root,
            env=self.env(ctx),
        )

    def readiness(self, ctx: Context) -> list[Check]:
        checks: list[Check] = []

        for tool, hint in (
            ("sam", "pip install aws-sam-cli"),
            ("aws", "https://aws.amazon.com/cli/"),
        ):
            try:
                shell.require(tool, hint)
                checks.append(Check(f"tool.{tool}", True, "installed"))
            except DeployError as e:
                checks.append(Check(f"tool.{tool}", False, str(e), fix=hint))

        if not (ctx.repo_root / "template.yaml").exists():
            checks.append(
                Check(
                    "template.present",
                    False,
                    "template.yaml not found",
                    fix="apply the aws/lambda overlay: action-platform cloud set aws/lambda",
                )
            )

            return checks

        try:
            stack = self._stack(ctx)
            checks.append(Check("stack.name", True, stack))
        except DeployError as e:
            checks.append(Check("stack.name", False, str(e), fix="fill samconfig.toml"))

            return checks

        try:
            env = self.env(ctx)
            identity = shell.aws(
                "sts", "get-caller-identity", region=self._region(ctx), env=env
            )
            checks.append(
                Check("aws.credentials", True, identity.get("Arn", "credentials work"))
            )
        except (DeployError, ProxyRefused) as e:
            checks.append(
                Check(
                    "aws.credentials",
                    False,
                    str(e),
                    fix="connect the deploy proxy or a role for this app in the AWS Lambda plugin",
                )
            )

            return checks

        checks.append(self._stack_state(ctx))
        checks.extend(self._permissions(ctx, env, identity, stack))
        checks.append(self._template_valid(ctx, env))

        return checks

    def _stack_state(self, ctx: Context) -> Check:
        status = self._status(ctx)

        if status is None:
            return Check("stack.state", True, "no stack yet; the deploy creates it")

        if status == "ROLLBACK_COMPLETE":
            return Check(
                "stack.state",
                True,
                f"{status}: the failed first creation is deleted before the deploy",
                severity="warning",
            )

        if status.endswith("_IN_PROGRESS"):
            return Check(
                "stack.state",
                False,
                f"{status}: another operation is running on the stack",
                fix="wait for it to finish, or cancel it in CloudFormation",
            )

        if status.endswith("_FAILED"):
            return Check(
                "stack.state",
                False,
                status,
                fix="the stack needs attention in CloudFormation before a deploy can update it",
            )

        return Check("stack.state", True, status)

    def _permissions(
        self, ctx: Context, env: dict[str, str] | None, identity: dict, stack: str
    ) -> list[Check]:
        role = (env or {}).get("AP_DEPLOY_ROLE") or _role_of(identity.get("Arn", ""))

        if not role:
            return []

        account = identity.get("Account", "")
        region = self._region(ctx) or "us-east-1"
        wanted: list[tuple[list[str], list[str]]] = [
            (
                ["cloudformation:CreateChangeSet", "cloudformation:DescribeStacks"],
                [f"arn:aws:cloudformation:{region}:{account}:stack/{stack}/*"],
            ),
            (
                ["lambda:CreateFunction", "lambda:UpdateFunctionCode"],
                [f"arn:aws:lambda:{region}:{account}:function:{stack}-ApiFunction"],
            ),
            (
                ["logs:CreateLogGroup"],
                [
                    f"arn:aws:logs:{region}:{account}:log-group:/aws/lambda/{stack}-ApiFunction"
                ],
            ),
        ]
        layers = self._layers(ctx, region)

        if layers:
            wanted.append((["lambda:GetLayerVersion"], layers))

        execution = (env or {}).get("AP_EXECUTION_ROLE")

        if execution:
            wanted.append((["iam:PassRole"], [execution]))

        denied: list[str] = []

        for actions, resources in wanted:
            try:
                data = shell.aws(
                    "iam",
                    "simulate-principal-policy",
                    "--policy-source-arn",
                    role,
                    "--action-names",
                    *actions,
                    "--resource-arns",
                    *resources,
                    env=env,
                )
            except DeployError as e:
                return [
                    Check(
                        "aws.permissions",
                        True,
                        f"not simulated: {str(e)[:200]}",
                        severity="warning",
                    )
                ]

            for row in data.get("EvaluationResults") or []:
                if row.get("EvalDecision") != "allowed":
                    denied.append(
                        f"{row.get('EvalActionName')} on {row.get('EvalResourceName')}"
                    )

        if denied:
            return [
                Check(
                    "aws.permissions",
                    False,
                    f"{role} may not: " + "; ".join(denied),
                    fix="widen the deploy role's policy (redeploy the deploy proxy when it created the role)",
                )
            ]

        return [Check("aws.permissions", True, f"{role} may deploy {stack}")]

    def _layers(self, ctx: Context, region: str) -> list[str]:
        text = (ctx.repo_root / "template.yaml").read_text(errors="replace")
        text = text.replace("${AWS::Region}", region)

        return sorted(
            set(re.findall(r"arn:aws:lambda:[\w-]+:\d{12}:layer:[\w-]+:\d+", text))
        )

    def _template_valid(self, ctx: Context, env: dict[str, str] | None) -> Check:
        try:
            shell.run(
                [*shell.require("sam", ""), "validate", "--lint"],
                cwd=ctx.repo_root,
                env=env,
            )
        except DeployError as e:
            return Check("template.valid", False, str(e), fix="fix template.yaml")

        return Check("template.valid", True, "sam validate --lint passes")

    def _status(self, ctx: Context) -> str | None:
        """The stack's CloudFormation status, or None when there is no stack."""
        try:
            data = shell.aws(
                "cloudformation",
                "describe-stacks",
                "--stack-name",
                self._stack(ctx),
                region=self._region(ctx),
                env=self.env(ctx),
            )
        except DeployError:
            return None

        rows = data.get("Stacks") or []

        return rows[0].get("StackStatus") if rows else None

    def _clear_failed_creation(self, ctx: Context) -> None:
        """A stack whose first creation failed sits in ROLLBACK_COMPLETE and refuses updates; it never existed, so it is deleted before the deploy creates it again."""
        if self._status(ctx) != "ROLLBACK_COMPLETE":
            return

        stack = self._stack(ctx)
        logger.info(
            "stack %s is ROLLBACK_COMPLETE: deleting it before the deploy", stack
        )
        region = self._region(ctx)
        env = self.env(ctx)
        shell.aws(
            "cloudformation",
            "delete-stack",
            "--stack-name",
            stack,
            region=region,
            env=env,
        )
        shell.aws(
            "cloudformation",
            "wait",
            "stack-delete-complete",
            "--stack-name",
            stack,
            region=region,
            env=env,
        )

    def deploy(self, ctx: Context) -> DeployResult:
        sam = shell.require("sam", "pip install aws-sam-cli")
        stage = self._stage(ctx)
        env = self.env(ctx)
        shell.run([*sam, "build"], cwd=ctx.repo_root, env=env)
        self._clear_failed_creation(ctx)
        args = [
            *sam,
            "deploy",
            "--no-confirm-changeset",
            "--no-fail-on-empty-changeset",
            "--stack-name",
            self._stack(ctx),
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
        """The stage's stack goes; once no stage is left, the app leaves the proxy too — its roles and grant — when the token may (`org.manage`)."""
        if self.diagnose(ctx).status != "missing":
            sam = shell.require("sam", "pip install aws-sam-cli")
            args = [*sam, "delete", "--no-prompts", "--stack-name", self._stack(ctx)]
            region = self._region(ctx)

            if region:
                args += ["--region", region]

            shell.run(args, cwd=ctx.repo_root, env=self.env(ctx))

        proxy = self.proxy(ctx)

        if proxy is None or self._other_stage_lives(ctx):
            return

        try:
            proxy.delete(token=proxy.token(ctx))
        except ProxyRefused as e:
            if e.status == 404:
                return

            if e.status == 403:
                raise DeployError(
                    f"the stacks are gone but the proxy still knows {proxy.app} ({e.detail}): "
                    "delete as an organization manager, or run `action-platform aws-lambda proxy delete`"
                ) from e

            raise

    def _other_stage_lives(self, ctx: Context) -> bool:
        other = replace(ctx, stage="dev" if ctx.stage == "prod" else "prod")

        return self.diagnose(other).status != "missing"

    def _url(self, ctx: Context) -> str | None:
        try:
            return self.diagnose(ctx).url
        except DeployError:
            return None


def _role_of(arn: str) -> str | None:
    found = re.match(r"^arn:aws:sts::(\d+):assumed-role/([^/]+)/", arn)

    return f"arn:aws:iam::{found.group(1)}:role/{found.group(2)}" if found else None


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
