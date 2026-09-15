"""`action-platform aws …`: the same reads as the tools, from the terminal."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from action_platform_plugin_aws import shell

app = typer.Typer(help="AWS: stacks, functions, Amplify jobs.", no_args_is_help=True)
console = Console()


def _table(columns: list[str], rows: list[list[str]]) -> None:
    table = Table(box=None, pad_edge=False)

    for c in columns:
        table.add_column(c)

    for r in rows:
        table.add_row(*r)

    console.print(table)


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


@app.command("jobs")
def jobs(
    app_id: str,
    branch: str = "main",
    limit: int = 5,
    region: str | None = typer.Option(None),
) -> None:
    """Amplify builds of a branch."""
    data = shell.aws(
        "amplify",
        "list-jobs",
        "--app-id",
        app_id,
        "--branch-name",
        branch,
        "--max-results",
        str(limit),
        region=region,
    )
    _table(
        ["job", "status", "type"],
        [
            [j["jobId"], j["status"], j.get("jobType", "")]
            for j in data.get("jobSummaries", [])
        ],
    )
