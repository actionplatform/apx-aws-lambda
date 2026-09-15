"""Running `aws` and `sam` — the two CLIs the targets drive — and reading their JSON."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from action_platform.core.exception import DeployError
from action_platform.settings import settings

MODULES = {"aws": "awscli", "sam": "samcli"}


def require(tool: str, hint: str) -> list[str]:
    """How to run `tool`: the binary on PATH, else the Python module the plugin depends on (`python -m awscli` / `python -m samcli`) — what makes the hosted platform work without the CLIs in its image."""
    path = shutil.which(tool)

    if path:
        return [path]

    module = MODULES.get(tool)

    if module and importlib.util.find_spec(module) is not None:
        return [sys.executable, "-m", module]

    raise DeployError(f"{tool} is not installed: {hint}")


def module_env(args: list[str], env: dict[str, str] | None) -> dict[str, str] | None:
    """`python -m awscli` in a child process must see the plugins volume the parent added with `site.addsitedir`; PYTHONPATH carries it over."""
    if args[:1] != [sys.executable] or settings.PLUGINS_DIR is None:
        return env

    merged = dict(env if env is not None else os.environ)
    current = merged.get("PYTHONPATH", "")
    plugins = str(settings.PLUGINS_DIR)

    if plugins not in current.split(os.pathsep):
        merged["PYTHONPATH"] = os.pathsep.join(p for p in (plugins, current) if p)

    return merged


def run(
    args: list[str], cwd: Path | None = None, env: dict[str, str] | None = None
) -> str:
    result = subprocess.run(
        args,
        cwd=cwd,
        env=module_env(args, env),
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        raise DeployError(
            f"{' '.join(args[:2])} failed: {(result.stderr or result.stdout).strip()[-2000:]}"
        )

    return result.stdout


def aws(
    *args: str,
    region: str | None = None,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> Any:
    command = [
        *require("aws", "https://aws.amazon.com/cli/"),
        *args,
        "--output",
        "json",
    ]

    if region:
        command += ["--region", region]

    out = run(command, cwd=cwd, env=env)

    return json.loads(out) if out.strip() else {}
