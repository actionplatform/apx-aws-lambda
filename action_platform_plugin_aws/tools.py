"""`aws.*` MCP tools: read-only looks at what is deployed. Deploying stays with the core's `deploy` tool, which drives the targets."""

from __future__ import annotations

from typing import Annotated, Any, Optional

from pydantic import BaseModel, Field

from action_platform.mcp.annotations import READ_ONLY

from action_platform_plugin_aws import shell

Region = Annotated[
    Optional[str], Field(description="AWS region; default from the environment")
]


class Stack(BaseModel):
    name: str
    status: str
    updated: Optional[str] = None


class Function(BaseModel):
    name: str
    runtime: Optional[str] = None
    memory: Optional[int] = None
    last_modified: Optional[str] = None


class Job(BaseModel):
    id: str
    status: str
    type: Optional[str] = None
    started: Optional[str] = None


def register_tools(mcp: Any) -> None:
    @mcp.tool(annotations=READ_ONLY)
    def stacks(
        prefix: Annotated[
            str, Field(description="Only stacks whose name starts with this")
        ] = "",
        region: Region = None,
    ) -> list[Stack]:
        """CloudFormation stacks — every aws/lambda deploy is one per stage."""
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

        return [
            Stack(
                name=s["StackName"],
                status=s["StackStatus"],
                updated=s.get("LastUpdatedTime"),
            )
            for s in data.get("StackSummaries", [])
            if s["StackName"].startswith(prefix)
        ]

    @mcp.tool(annotations=READ_ONLY)
    def functions(
        prefix: Annotated[
            str, Field(description="Only functions whose name starts with this")
        ] = "",
        region: Region = None,
    ) -> list[Function]:
        """Lambda functions and their runtime."""
        data = shell.aws("lambda", "list-functions", region=region)

        return [
            Function(
                name=f["FunctionName"],
                runtime=f.get("Runtime"),
                memory=f.get("MemorySize"),
                last_modified=f.get("LastModified"),
            )
            for f in data.get("Functions", [])
            if f["FunctionName"].startswith(prefix)
        ]

    @mcp.tool(annotations=READ_ONLY)
    def amplify_jobs(
        app_id: str, branch: str = "main", limit: int = 5, region: Region = None
    ) -> list[Job]:
        """The last builds of an Amplify branch."""
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

        return [
            Job(
                id=j["jobId"],
                status=j["status"],
                type=j.get("jobType"),
                started=j.get("startTime"),
            )
            for j in data.get("jobSummaries", [])
        ]
