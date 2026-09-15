"""The plugin: the aws/lambda overlay, `action-platform aws-lambda` commands, `aws_lambda_*` tools."""

from __future__ import annotations

from pathlib import Path

from action_platform.abc import Plugin, Surface

from apx_aws_lambda import cli
from apx_aws_lambda.tools import register_tools


class AwsLambdaPlugin(Plugin):
    slug = "aws-lambda"
    description = (
        "Deploy to AWS Lambda with SAM; read CloudFormation stacks and functions"
    )
    min_core = "0.17"
    needs = [
        "tool: aws (AWS CLI v2), sam (AWS SAM CLI)",
        "env: AWS_REGION; credentials from role_arn (OIDC, no keys) or the AWS CLI chain (SSO, profile, instance role)",
        "net: *.amazonaws.com",
    ]

    @property
    def overlays(self) -> Path:
        return Path(__file__).parent / "overlays"

    def register(self, surface: Surface) -> None:
        if surface.mcp is not None:
            register_tools(surface.mcp)

        if surface.cli is not None:
            surface.cli.add_typer(cli.app, name="aws-lambda")
