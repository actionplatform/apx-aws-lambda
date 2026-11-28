"""The function's `live` alias through the AWS CLI: read it, point it at a version with a weighted second one, list and delete versions, read a version's CloudWatch metrics."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from action_platform.core.exception import DeployError

from apx_aws_lambda import shell
from apx_aws_lambda.abc import Access, Alias
from apx_aws_lambda.spec import Spec

ALIAS = "live"
METRICS = ("Errors", "Throttles", "Invocations")


class LambdaAlias(Alias):
    def __init__(self, spec: Spec, access: Access) -> None:
        self.spec = spec
        self.access = access

    def _aws(self, *args: str) -> dict:
        return shell.aws(*args, region=self.spec.region, env=self.access.env)

    def read(self) -> dict:
        try:
            row = self._aws(
                "lambda",
                "get-alias",
                "--function-name",
                self.spec.function,
                "--name",
                ALIAS,
            )
        except DeployError as e:
            raise DeployError(
                f"{self.spec.function} has no `{ALIAS}` alias ({str(e)[-200:]}): "
                "apply the aws/lambda overlay again (Configuration → Deploy target, "
                "or action-platform cloud set aws/lambda) and deploy"
            ) from e

        weights = (row.get("RoutingConfig") or {}).get("AdditionalVersionWeights") or {}

        return {"version": row["FunctionVersion"], "weights": dict(weights)}

    def point(self, version: str, weights: dict[str, float] | None = None) -> None:
        self._aws(
            "lambda",
            "update-alias",
            "--function-name",
            self.spec.function,
            "--name",
            ALIAS,
            "--function-version",
            version,
            "--routing-config",
            json.dumps({"AdditionalVersionWeights": weights or {}}),
        )

    def versions(self) -> list[str]:
        rows = self._aws(
            "lambda", "list-versions-by-function", "--function-name", self.spec.function
        ).get("Versions", [])

        return sorted(
            (r["Version"] for r in rows if r.get("Version", "").isdigit()), key=int
        )

    def delete_version(self, version: str) -> None:
        self._aws(
            "lambda",
            "delete-function",
            "--function-name",
            self.spec.function,
            "--qualifier",
            version,
        )

    def errors(self, version: str, window: int) -> dict[str, float]:
        end = datetime.now(timezone.utc)
        start = end - timedelta(seconds=window)
        found: dict[str, float] = {}

        for metric in METRICS:
            data = self._aws(
                "cloudwatch",
                "get-metric-statistics",
                "--namespace",
                "AWS/Lambda",
                "--metric-name",
                metric,
                "--dimensions",
                f"Name=FunctionName,Value={self.spec.function}",
                f"Name=Resource,Value={self.spec.function}:{version}",
                "--start-time",
                start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "--end-time",
                end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "--period",
                str(max(60, window // 60 * 60)),
                "--statistics",
                "Sum",
            )
            found[metric] = sum(p.get("Sum", 0) for p in data.get("Datapoints") or [])

        return found
