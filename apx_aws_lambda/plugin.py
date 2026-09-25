"""The plugin: the aws/lambda overlay, `action-platform aws-lambda` commands, `aws_lambda_*` tools."""

from __future__ import annotations

from pathlib import Path

from action_platform.abc import Option, Plugin, Surface

from apx_aws_lambda import cli
from apx_aws_lambda.tools import register_tools


class AwsLambdaPlugin(Plugin):
    slug = "aws-lambda"
    name = "AWS Lambda"
    description = (
        "Deploy to AWS Lambda with SAM; read CloudFormation stacks and functions"
    )
    min_core = "0.17.9"
    needs = [
        "tool: aws, sam (bundled as Python packages when not on PATH)",
        "env: AWS_REGION; credentials from role_arn (the connected account's deploy role, OIDC, no keys) or the AWS CLI chain (SSO, profile, instance role)",
        "net: *.amazonaws.com",
    ]
    options = [
        Option(
            "role_arn",
            "Deploy role ARN",
            "text",
            help=(
                "The deploy role of your connected AWS account: the platform's token assumes it, and the token's session tag keeps every deploy inside its app, so the platform never holds an access key and nothing runs in your account. "
                "Create it once with the IAM-only stack apx_aws_lambda/connect/template.yaml (CloudFormation console, Upload a template file) or `action-platform aws-lambda connect <platform url> <organization>`; the DeployRoleArn output goes here."
            ),
            required=True,
        ),
    ]

    @property
    def overlays(self) -> Path:
        return Path(__file__).parent / "overlays"

    def register(self, surface: Surface) -> None:
        if surface.mcp is not None:
            register_tools(surface.mcp)

        if surface.cli is not None:
            surface.cli.add_typer(cli.app, name="aws-lambda")
