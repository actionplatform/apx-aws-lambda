"""`action-platform aws-lambda …`: the same reads as the tools, from the terminal."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from apx_aws_lambda import shell

app = typer.Typer(
    help="AWS Lambda: connect an account, stacks, functions.",
    no_args_is_help=True,
)
console = Console()
CONNECT_TEMPLATE = Path(__file__).parent / "connect" / "template.yaml"


def _table(columns: list[str], rows: list[list[str]]) -> None:
    table = Table(box=None, pad_edge=False)

    for c in columns:
        table.add_column(c)

    for r in rows:
        table.add_row(*r)

    console.print(table)


@app.command("connect")
def connect(
    issuer: str = typer.Argument(..., help="The platform's public url"),
    organization: str = typer.Argument(..., help="The organization slug"),
    region: str = typer.Option("us-east-1", help="Region the stack lives in"),
    stack_name: str = typer.Option("action-platform-connect"),
    oidc_provider_arn: str = typer.Option(
        "", help="An IAM OIDC provider for the platform that already exists"
    ),
) -> None:
    """Connect the AWS account the CLI is logged in to: the IAM-only stack, once."""
    shell.run(
        [
            *shell.require("aws", "https://aws.amazon.com/cli/"),
            "cloudformation",
            "deploy",
            "--template-file",
            str(CONNECT_TEMPLATE),
            "--stack-name",
            stack_name,
            "--region",
            region,
            "--capabilities",
            "CAPABILITY_NAMED_IAM",
            "--no-fail-on-empty-changeset",
            "--parameter-overrides",
            f"IssuerUrl={issuer.rstrip('/')}",
            f"Organization={organization}",
            f"OidcProviderArn={oidc_provider_arn}",
        ]
    )
    data = shell.aws(
        "cloudformation", "describe-stacks", "--stack-name", stack_name, region=region
    )
    outputs = {
        o["OutputKey"]: o["OutputValue"]
        for o in (data.get("Stacks") or [{}])[0].get("Outputs") or []
    }
    console.print(f"[bold]DeployRoleArn[/bold]  {outputs.get('DeployRoleArn', '-')}")
    console.print(
        "On the platform: Plugins → AWS Lambda → Configure → Deploy role ARN."
    )


@app.command("stacks")
def stacks(
    prefix: str = typer.Argument(""), region: str | None = typer.Option(None)
) -> None:
    """CloudFormation stacks."""
    data = shell.aws(
        "cloudformation",
        "list-stacks",
        "--stack-status-filter",
        "CREATE_COMPLETE",
        "UPDATE_COMPLETE",
        "ROLLBACK_COMPLETE",
        "UPDATE_ROLLBACK_COMPLETE",
        region=region,
    )
    _table(
        ["stack", "status", "updated"],
        [
            [s["StackName"], s["StackStatus"], s.get("LastUpdatedTime", "")]
            for s in data.get("StackSummaries", [])
            if s["StackName"].startswith(prefix)
        ],
    )


@app.command("functions")
def functions(
    prefix: str = typer.Argument(""), region: str | None = typer.Option(None)
) -> None:
    """Lambda functions."""
    data = shell.aws("lambda", "list-functions", region=region)
    _table(
        ["function", "runtime", "memory"],
        [
            [f["FunctionName"], f.get("Runtime", ""), str(f.get("MemorySize", ""))]
            for f in data.get("Functions", [])
            if f["FunctionName"].startswith(prefix)
        ],
    )
