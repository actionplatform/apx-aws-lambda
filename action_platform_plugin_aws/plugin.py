"""The plugin: overlays for the two targets, `action-platform aws` commands, `aws.*` tools."""

from __future__ import annotations

from pathlib import Path

from action_platform.abc import Plugin, Surface

from action_platform_plugin_aws import cli
from action_platform_plugin_aws.tools import register_tools


class AwsPlugin(Plugin):
    slug = "aws"
    description = "Deploy to AWS Lambda (SAM) and Amplify Hosting; read stacks, functions and Amplify jobs"
    min_core = "0.16"
    needs = [
        "tool: aws (AWS CLI v2); sam (AWS SAM CLI) for aws/lambda",
        "env: AWS_PROFILE or AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY, AWS_REGION",
        "net: *.amazonaws.com",
    ]

    @property
    def overlays(self) -> Path:
        return Path(__file__).parent / "overlays"

    def register(self, surface: Surface) -> None:
        if surface.mcp is not None:
            register_tools(surface.mcp)

        if surface.cli is not None:
            surface.cli.add_typer(cli.app, name="aws")
