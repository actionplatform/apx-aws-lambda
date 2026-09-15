"""`action-platform aws-lambda …`: the same reads as the tools, from the terminal."""

from __future__ import annotations

import typer
from action_platform.core.exception import ActionPlatformError
from rich.console import Console
from rich.table import Table

from apx_aws_lambda import shell
from apx_aws_lambda.proxy import ProxyClient

app = typer.Typer(
    help="AWS Lambda: stacks, functions, the deploy proxy.", no_args_is_help=True
)
proxy_app = typer.Typer(
    help="The deploy proxy in your account: apps and grants.", no_args_is_help=True
)
app.add_typer(proxy_app, name="proxy")
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


def _client(proxy: str, app_path: str) -> ProxyClient:
    try:
        return ProxyClient(proxy, app_path)
    except ActionPlatformError as e:
        raise typer.BadParameter(str(e)) from e


def _show(data: dict) -> None:
    for key in ("app", "region", "stack_prefix", "deploy_role", "execution_role"):
        if data.get(key):
            console.print(f"[bold]{key}[/bold]  {data[key]}")

    console.print(
        "[bold]subjects[/bold]  " + ", ".join(data.get("subjects") or []) or "-"
    )


@proxy_app.command("health")
def proxy_health(proxy: str = typer.Argument(..., help="Proxy url")) -> None:
    """Version, issuer and organization the proxy serves."""
    for key, value in ProxyClient(proxy, "a/b/c").health().items():
        console.print(f"[bold]{key}[/bold]  {value}")


@proxy_app.command("create")
def proxy_create(
    proxy: str = typer.Argument(..., help="Proxy url"),
    app_path: str = typer.Argument(..., metavar="ORG/PROJECT/APP"),
    region: str | None = typer.Option(None, help="Region the app deploys to"),
) -> None:
    """Create the app's deploy and execution roles (needs org.manage)."""
    _show(_client(proxy, app_path).create(region))


@proxy_app.command("show")
def proxy_show(
    proxy: str = typer.Argument(..., help="Proxy url"),
    app_path: str = typer.Argument(..., metavar="ORG/PROJECT/APP"),
) -> None:
    """Roles and grants of an app."""
    _show(_client(proxy, app_path).show())


@proxy_app.command("grant")
def proxy_grant(
    proxy: str = typer.Argument(..., help="Proxy url"),
    app_path: str = typer.Argument(..., metavar="ORG/PROJECT/APP"),
    subjects: list[str] = typer.Argument(
        ...,
        help="Subject prefixes that may deploy: org:<org>, org:<org>:project:<p>, the app's own",
    ),
) -> None:
    """Replace who may deploy the app (needs org.manage)."""
    _show(_client(proxy, app_path).grant(subjects))


@proxy_app.command("delete")
def proxy_delete(
    proxy: str = typer.Argument(..., help="Proxy url"),
    app_path: str = typer.Argument(..., metavar="ORG/PROJECT/APP"),
) -> None:
    """Delete the app's roles and grants (needs org.manage)."""
    _client(proxy, app_path).delete()
    console.print(f"deleted {app_path}")
