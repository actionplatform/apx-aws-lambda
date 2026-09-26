"""The plugin: the aws/lambda overlay, `action-platform aws-lambda` commands, `aws_lambda_*` tools."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from action_platform.abc import Option, Plugin, Surface

from apx_aws_lambda import cli
from apx_aws_lambda.tools import register_tools

CLOUDSHELL = "https://console.aws.amazon.com/cloudshell/home?region=us-east-1"


def connect_command() -> str:
    """What the user pastes in CloudShell: the connect stack from this release's template on GitHub, then the account ID the platform asks for."""
    try:
        ref = f"v{version('apx-aws-lambda')}"
    except PackageNotFoundError:
        ref = "master"

    template = f"https://raw.githubusercontent.com/actionplatform/apx-aws-lambda/{ref}/apx_aws_lambda/connect/template.yaml"

    return (
        f"curl -fsSL {template} -o ap-connect.yaml"
        " && aws cloudformation deploy --region us-east-1"
        " --stack-name action-platform-connect --template-file ap-connect.yaml"
        " --capabilities CAPABILITY_NAMED_IAM --no-fail-on-empty-changeset"
        " --parameter-overrides IssuerUrl={issuer} Organization={organization}"
        " && aws sts get-caller-identity --query Account --output text"
    )


class AwsLambdaPlugin(Plugin):
    slug = "aws-lambda"
    name = "AWS Lambda"
    description = (
        "Deploy to AWS Lambda with SAM; read CloudFormation stacks and functions"
    )
    min_core = "0.32.0"
    needs = [
        "tool: aws, sam (bundled as Python packages when not on PATH)",
        "env: AWS_REGION; credentials from role_arn (the connected account's deploy role, OIDC, no keys) or the AWS CLI chain (SSO, profile, instance role)",
        "net: *.amazonaws.com",
    ]
    options = [
        Option(
            "role_arn",
            "AWS account ID",
            "text",
            help="Paste the account ID the command prints.",
            required=True,
            action_label="Connect AWS",
            action_url=CLOUDSHELL,
            action_copy=connect_command(),
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
