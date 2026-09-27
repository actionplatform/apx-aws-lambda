"""The scope's CloudFormation stack: its status and outputs, clearing a first creation that failed, rolling back."""

from __future__ import annotations

from action_platform.core.exception import DeployError
from action_platform.logging import logger

from apx_aws_lambda import shell
from apx_aws_lambda.abc import Access, Stack
from apx_aws_lambda.spec import Spec

FAILED_CREATION = ("ROLLBACK_COMPLETE", "ROLLBACK_FAILED")


class CloudFormationStack(Stack):
    def __init__(self, spec: Spec, access: Access) -> None:
        self.spec = spec
        self.env = access.env

    def _aws(self, *args: str) -> dict:
        return shell.aws(*args, region=self.spec.region, env=self.env)

    def describe(self) -> dict | None:
        try:
            data = self._aws(
                "cloudformation", "describe-stacks", "--stack-name", self.spec.stack
            )
        except DeployError:
            return None

        rows = data.get("Stacks") or []

        return rows[0] if rows else None

    def status(self) -> str | None:
        row = self.describe()

        return row.get("StackStatus") if row else None

    def clear_failed_creation(self) -> None:
        """A stack whose first creation failed sits in ROLLBACK_COMPLETE — or ROLLBACK_FAILED when a resource could not be rolled back — and refuses updates; it never existed, so it is deleted before the deploy creates it again. When that delete fails on a resource that was never created, it is deleted again with that resource retained."""
        status = self.status()

        if status not in FAILED_CREATION:
            return

        stack = self.spec.stack
        logger.info("stack %s is %s: deleting it before the deploy", stack, status)
        self._delete()

        if self.status() != "DELETE_FAILED":
            return

        stuck = [
            row["LogicalResourceId"]
            for row in self._aws(
                "cloudformation", "describe-stack-resources", "--stack-name", stack
            ).get("StackResources", [])
            if row.get("ResourceStatus") == "DELETE_FAILED"
        ]
        logger.info("stack %s kept %s: deleting it without them", stack, stuck)
        self._delete(retain=stuck)

    def rollback(self) -> None:
        self._aws("cloudformation", "rollback-stack", "--stack-name", self.spec.stack)

    def _delete(self, retain: list[str] | None = None) -> None:
        """Delete and wait; a delete that fails leaves the stack in DELETE_FAILED for the caller to look at."""
        args = ["cloudformation", "delete-stack", "--stack-name", self.spec.stack]

        if retain:
            args += ["--retain-resources", *retain]

        self._aws(*args)

        try:
            self._aws(
                "cloudformation",
                "wait",
                "stack-delete-complete",
                "--stack-name",
                self.spec.stack,
            )
        except DeployError:
            if retain:
                raise


def outputs_of(row: dict | None) -> dict[str, str]:
    return {
        o["OutputKey"]: o["OutputValue"] for o in ((row or {}).get("Outputs") or [])
    }


def url_of(outputs: dict[str, str]) -> str | None:
    return (
        outputs.get("ApiUrl")
        or outputs.get("HttpApiUrl")
        or next((v for k, v in outputs.items() if "url" in k.lower()), None)
    )
