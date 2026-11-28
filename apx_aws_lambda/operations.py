"""The steps a deployment strategy asks of `aws/lambda`, over Lambda versions and the weighted `live` alias the HTTP API invokes. Shadow needs `mirror`, which Lambda cannot do: an alias splits invocations between versions but cannot copy them."""

from __future__ import annotations

from typing import TYPE_CHECKING

from action_platform.abc import TargetOperations
from action_platform.core.exception import DeployError
from action_platform.core.rollout.operations import OperationContext, OperationResult

from apx_aws_lambda.stack import outputs_of, url_of

if TYPE_CHECKING:
    from apx_aws_lambda.lambda_ import LambdaTarget


class LambdaOperations(TargetOperations):
    name = "aws/lambda"

    def __init__(self, target: LambdaTarget) -> None:
        self.target = target

    def supports(self) -> set[str]:
        return {
            "deploy_new",
            "shift",
            "replace",
            "retire_old",
            "rollback",
            "health",
            "status",
        }

    def mirror(self, ctx: OperationContext) -> OperationResult:
        return OperationResult(
            False,
            "aws/lambda cannot mirror: a Lambda alias splits invocations between versions but cannot copy them, so shadow is unsupported",
        )

    def deploy_new(self, ctx: OperationContext) -> OperationResult:
        return self._run(ctx, self._deploy_new)

    def shift(self, ctx: OperationContext) -> OperationResult:
        return self._run(ctx, self._shift)

    def replace(self, ctx: OperationContext) -> OperationResult:
        return self._run(ctx, self._shift)

    def retire_old(self, ctx: OperationContext) -> OperationResult:
        return self._run(ctx, self._retire_old)

    def rollback(self, ctx: OperationContext) -> OperationResult:
        return self._run(ctx, self._rollback)

    def health(self, ctx: OperationContext) -> OperationResult:
        return self._run(ctx, self._health)

    def status(self, ctx: OperationContext) -> OperationResult:
        return self._run(ctx, self._status)

    def _run(self, ctx: OperationContext, step) -> OperationResult:
        try:
            spec = self.target.spec(ctx.deploy)
            access = self.target.credentials(ctx.deploy, spec).access(spec)

            return step(ctx, spec, self.target.parts(spec, access))
        except DeployError as e:
            return OperationResult(False, str(e)[-500:])

    def _deploy_new(self, ctx, spec, parts) -> OperationResult:
        previous = self._current(parts)
        result = self.target.deploy(ctx.deploy)
        new = parts.alias.read()["version"]
        weight = int(ctx.params.get("weight", 100))

        if previous is not None and new != previous and weight < 100:
            parts.alias.point(previous, {new: weight / 100})

        data = {"new_version": new, "previous_version": previous or new}

        if not result.ok:
            return OperationResult(False, result.error or "deploy failed", new, data)

        if previous is None or new == previous:
            return OperationResult(
                True, f"version {new} is live; nothing changed", new, data
            )

        return OperationResult(
            True, f"version {new} published at {weight}% beside {previous}", new, data
        )

    def _shift(self, ctx, spec, parts) -> OperationResult:
        weight = int(ctx.params.get("weight", ctx.params.get("upto", 100)))
        now = parts.alias.read()
        new = self._new(ctx, now)
        primary = self._previous(ctx, now, new)

        if weight >= 100:
            parts.alias.point(new)

            return OperationResult(
                True, f"{new} serves 100%", new, {"new_version": new}
            )

        if primary is None:
            return OperationResult(True, f"{new} is the only version", new)

        parts.alias.point(primary, {new: weight / 100})

        return OperationResult(
            True,
            f"{new} serves {weight}%, {primary} {100 - weight}%",
            new,
            {"new_version": new, "previous_version": primary},
        )

    def _retire_old(self, ctx, spec, parts) -> OperationResult:
        now = parts.alias.read()
        new = self._new(ctx, now)
        parts.alias.point(new)
        keep = spec.options.retain_versions
        removed: list[str] = []

        if keep is not None:
            published = parts.alias.versions()
            stale = [v for v in published if v != new][: max(0, len(published) - keep)]

            for version in stale:
                parts.alias.delete_version(version)
                removed.append(version)

        detail = f"{new} serves 100%"

        if removed:
            detail += f"; deleted versions {', '.join(removed)}"

        return OperationResult(
            True, detail, new, {"new_version": new, "deleted_versions": removed}
        )

    def _rollback(self, ctx, spec, parts) -> OperationResult:
        now = parts.alias.read()
        new = self._new(ctx, now)
        previous = self._previous(ctx, now, new)

        if previous is None:
            earlier = [v for v in parts.alias.versions() if int(v) < int(new)]

            if not earlier:
                return OperationResult(False, "there is no earlier version")

            previous = earlier[-1]

        parts.alias.point(previous)

        return OperationResult(
            True,
            f"{previous} serves 100%",
            previous,
            {"previous_version": previous, "rolled_back_from": new},
        )

    def _health(self, ctx, spec, parts) -> OperationResult:
        new = self._new(ctx, parts.alias.read())
        options = spec.options
        found = parts.alias.errors(new, options.health_window)
        data = {"version": new, **found}
        problems = []

        if found["Errors"] > options.max_errors:
            problems.append(f"{int(found['Errors'])} errors (max {options.max_errors})")

        if found["Throttles"] > options.max_throttles:
            problems.append(
                f"{int(found['Throttles'])} throttles (max {options.max_throttles})"
            )

        url = url_of(outputs_of(parts.stack.describe()))

        if url and options.health:
            unhealthy = self.target.health.answers(url.rstrip("/") + options.health)

            if unhealthy:
                problems.append(unhealthy)

        if problems:
            return OperationResult(
                False, f"version {new}: " + "; ".join(problems), new, data
            )

        return OperationResult(
            True,
            f"version {new}: {int(found['Invocations'])} invocations, no errors or throttles in {options.health_window}s",
            new,
            data,
        )

    def _status(self, ctx, spec, parts) -> OperationResult:
        now = parts.alias.read()
        shares = ", ".join(f"{v} {round(w * 100)}%" for v, w in now["weights"].items())
        detail = f"live serves {now['version']}" + (
            f", {shares}" if shares else " at 100%"
        )

        return OperationResult(True, detail, now["version"], dict(now))

    def _current(self, parts) -> str | None:
        try:
            return parts.alias.read()["version"]
        except DeployError:
            return None

    def _new(self, ctx: OperationContext, now: dict) -> str:
        known = ctx.data.get("new_version") or ctx.external_ref

        if known:
            return str(known)

        return next(iter(now["weights"]), now["version"])

    def _previous(self, ctx: OperationContext, now: dict, new: str) -> str | None:
        known = ctx.data.get("previous_version")

        if known:
            return str(known)

        return now["version"] if now["version"] != new else None
