"""`aws/lambda`: a SAM stack per stage. Deploy = `sam build` + `sam deploy --config-env <stage>`; rollback = CloudFormation rollback to the previous stack state; diagnose = stack status and the HTTP API url."""

from __future__ import annotations

import os
import tomllib

from action_platform.abc import DeployTarget
from action_platform.core.context import Context, DeployResult, Diagnosis
from action_platform.core.exception import DeployError

from apx_aws_lambda import shell


class LambdaTarget(DeployTarget):
    name = "aws/lambda"

    def __init__(
        self, region: str | None = None, config: str = "samconfig.toml", **_: object
    ) -> None:
        self.region = (
            region
            or os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
        )
        self.config = config

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
        shell.aws("sts", "get-caller-identity", region=self._region(ctx))
        shell.run([shell.require("sam", ""), "validate", "--lint"], cwd=ctx.repo_root)

    def deploy(self, ctx: Context) -> DeployResult:
        sam = shell.require("sam", "pip install aws-sam-cli")
        stage = self._stage(ctx)
        shell.run([sam, "build"], cwd=ctx.repo_root)
        args = [sam, "deploy", "--no-confirm-changeset", "--no-fail-on-empty-changeset"]

        if stage != "default":
            args += ["--config-env", stage]

        shell.run(args, cwd=ctx.repo_root)
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
        args = [sam, "delete", "--no-prompts", "--stack-name", self._stack(ctx)]
        region = self._region(ctx)

        if region:
            args += ["--region", region]

        shell.run(args, cwd=ctx.repo_root)

    def _url(self, ctx: Context) -> str | None:
        try:
            return self.diagnose(ctx).url
        except DeployError:
            return None
