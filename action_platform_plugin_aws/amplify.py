"""`aws/amplify`: Amplify Hosting builds from the connected branch. Deploy = start a job on the branch and wait; rollback = redeploy the previous succeeded job; diagnose = the branch's last job and its url."""

from __future__ import annotations

import os
import time

from action_platform.abc import DeployTarget
from action_platform.core.context import Context, DeployResult, Diagnosis
from action_platform.core.exception import DeployError

from action_platform_plugin_aws import shell


class AmplifyTarget(DeployTarget):
    name = "aws/amplify"

    def __init__(
        self,
        app_id: str | None = None,
        region: str | None = None,
        timeout: int = 900,
        **_: object,
    ) -> None:
        self.app_id = app_id or os.environ.get("AMPLIFY_APP_ID")
        self.region = (
            region
            or os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
        )
        self.timeout = timeout

    def _app(self) -> str:
        if not self.app_id:
            raise DeployError(
                "aws/amplify needs app_id under [deploy] or AMPLIFY_APP_ID"
            )

        return self.app_id

    def _branch(self, ctx: Context) -> str:
        return ctx.branch or "main"

    def preflight(self, ctx: Context) -> None:
        shell.require("aws", "https://aws.amazon.com/cli/")
        shell.aws(
            "amplify",
            "get-branch",
            "--app-id",
            self._app(),
            "--branch-name",
            self._branch(ctx),
            region=self.region,
        )

    def deploy(self, ctx: Context) -> DeployResult:
        branch = self._branch(ctx)
        started = shell.aws(
            "amplify",
            "start-job",
            "--app-id",
            self._app(),
            "--branch-name",
            branch,
            "--job-type",
            "RELEASE",
            "--job-reason",
            f"action-platform {ctx.next_version}",
            region=self.region,
        )
        job_id = started["jobSummary"]["jobId"]
        status = self._wait(branch, job_id)
        ok = status == "SUCCEED"

        return DeployResult(
            ok=ok,
            target=self.name,
            version=ctx.next_version,
            url=self._url(branch),
            error=None if ok else f"job {job_id} ended {status}",
        )

    def rollback(self, ctx: Context, to_version: str | None = None) -> None:
        branch = self._branch(ctx)
        jobs = shell.aws(
            "amplify",
            "list-jobs",
            "--app-id",
            self._app(),
            "--branch-name",
            branch,
            region=self.region,
        )
        done = [j for j in jobs.get("jobSummaries", []) if j.get("status") == "SUCCEED"]

        if len(done) < 2:
            raise DeployError("no earlier succeeded job to return to")

        shell.aws(
            "amplify",
            "start-job",
            "--app-id",
            self._app(),
            "--branch-name",
            branch,
            "--job-type",
            "RETRY",
            "--job-id",
            done[1]["jobId"],
            region=self.region,
        )

    def diagnose(self, ctx: Context) -> Diagnosis:
        branch = self._branch(ctx)

        try:
            jobs = shell.aws(
                "amplify",
                "list-jobs",
                "--app-id",
                self._app(),
                "--branch-name",
                branch,
                "--max-results",
                "1",
                region=self.region,
            )
        except DeployError as e:
            return Diagnosis(
                ok=False,
                target=self.name,
                status="unreachable",
                details={"error": str(e)},
            )

        rows = jobs.get("jobSummaries") or []
        last = rows[0] if rows else {}
        status = last.get("status", "none")

        return Diagnosis(
            ok=status == "SUCCEED",
            target=self.name,
            status=status,
            url=self._url(branch),
            details={
                "job": last.get("jobId", ""),
                "branch": branch,
                "app": self._app(),
            },
        )

    def _wait(self, branch: str, job_id: str) -> str:
        deadline = time.time() + self.timeout

        while time.time() < deadline:
            job = shell.aws(
                "amplify",
                "get-job",
                "--app-id",
                self._app(),
                "--branch-name",
                branch,
                "--job-id",
                job_id,
                region=self.region,
            )
            status = job["job"]["summary"]["status"]

            if status in {"SUCCEED", "FAILED", "CANCELLED"}:
                return status

            time.sleep(10)

        return "TIMEOUT"

    def _url(self, branch: str) -> str | None:
        try:
            app = shell.aws(
                "amplify", "get-app", "--app-id", self._app(), region=self.region
            )["app"]
        except DeployError:
            return None

        domain = app.get("defaultDomain")

        return f"https://{branch}.{domain}" if domain else None
