"""The SAM CLI on the repository: build, deploy with the scope's stack, overrides and tags, delete, validate. Its output streams to the job log (`shell.run`)."""

from __future__ import annotations

from apx_aws_lambda import shell
from apx_aws_lambda.abc import Access, Sam
from apx_aws_lambda.spec import Spec

HINT = "pip install aws-sam-cli"


class SamCli(Sam):
    def __init__(self, spec: Spec, access: Access) -> None:
        self.spec = spec
        self.env = access.env

    def _sam(self) -> list[str]:
        return shell.require("sam", HINT)

    def build(self) -> None:
        shell.run([*self._sam(), "build"], cwd=self.spec.root, env=self.env)

    def deploy(self) -> None:
        args = [
            *self._sam(),
            "deploy",
            "--no-confirm-changeset",
            "--no-fail-on-empty-changeset",
            "--stack-name",
            self.spec.stack,
        ]

        if self.spec.config_env != "default":
            args += ["--config-env", self.spec.config_env]

        args += self.spec.overrides
        args += self.spec.tags
        shell.run(args, cwd=self.spec.root, env=self.env)

    def delete(self) -> None:
        args = [*self._sam(), "delete", "--no-prompts", "--stack-name", self.spec.stack]

        if self.spec.region:
            args += ["--region", self.spec.region]

        shell.run(args, cwd=self.spec.root, env=self.env)

    def validate(self) -> None:
        shell.run(
            [*shell.require("sam", ""), "validate", "--lint"],
            cwd=self.spec.root,
            env=self.env,
        )
