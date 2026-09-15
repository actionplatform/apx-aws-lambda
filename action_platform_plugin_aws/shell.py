"""Running `aws` and `sam` — the two CLIs the targets drive — and reading their JSON."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from action_platform.core.exception import DeployError


def require(tool: str, hint: str) -> str:
    path = shutil.which(tool)

    if not path:
        raise DeployError(f"{tool} is not installed: {hint}")

    return path


def run(
    args: list[str], cwd: Path | None = None, env: dict[str, str] | None = None
) -> str:
    result = subprocess.run(
        args, cwd=cwd, env=env, capture_output=True, text=True, check=False
    )

    if result.returncode != 0:
        raise DeployError(
            f"{' '.join(args[:2])} failed: {(result.stderr or result.stdout).strip()[-2000:]}"
        )

    return result.stdout


def aws(*args: str, region: str | None = None, cwd: Path | None = None) -> Any:
    command = [require("aws", "https://aws.amazon.com/cli/"), *args, "--output", "json"]

    if region:
        command += ["--region", region]

    out = run(command, cwd=cwd)

    return json.loads(out) if out.strip() else {}
