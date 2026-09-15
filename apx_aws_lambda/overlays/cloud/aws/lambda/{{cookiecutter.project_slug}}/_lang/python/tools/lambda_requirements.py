"""Requirements for the Lambda layer without poetry on the build host: pins from poetry.lock when it exists, otherwise the main dependencies of pyproject.toml (PEP 621 or poetry's caret/tilde constraints turned into PEP 440)."""

import re
import sys
from pathlib import Path

import tomllib


def caret(version: str) -> str:
    parts = [int(p) for p in re.findall(r"\d+", version)]
    upper = list(parts)

    for i, p in enumerate(parts):
        if p != 0 or i == len(parts) - 1:
            upper = parts[:i] + [p + 1] + [0] * (len(parts) - i - 1)
            break

    return f">={version},<{'.'.join(str(p) for p in upper)}"


def tilde(version: str) -> str:
    parts = [int(p) for p in re.findall(r"\d+", version)]
    upper = parts[:1] + [parts[1] + 1] if len(parts) > 1 else [parts[0] + 1]

    return f">={version},<{'.'.join(str(p) for p in upper)}"


def constraint(spec: str) -> str:
    spec = spec.strip()

    if spec in ("*", ""):
        return ""

    if spec.startswith("^"):
        return caret(spec[1:])

    if spec.startswith("~") and not spec.startswith("~="):
        return tilde(spec[1:])

    if re.match(r"^\d", spec):
        return f"=={spec}"

    return spec


def from_poetry(deps: dict) -> list[str]:
    rows = []

    for name, spec in deps.items():
        if name.lower() == "python":
            continue

        extras = ""
        version = spec

        if isinstance(spec, dict):
            if spec.get("optional"):
                continue

            version = spec.get("version", "*")
            extras = (
                f"[{','.join(spec['extras'])}]" if spec.get("extras") else ""
            )

        rows.append(f"{name}{extras}{constraint(str(version))}")

    return rows


def main(out: str) -> None:
    lock = Path("poetry.lock")

    if lock.exists():
        data = tomllib.loads(lock.read_text())
        rows = [
            f"{p['name']}=={p['version']}"
            for p in data.get("package", [])
            if "main" in (p.get("groups") or ["main"])
        ]
    else:
        pyproject = tomllib.loads(Path("pyproject.toml").read_text())
        rows = list(pyproject.get("project", {}).get("dependencies") or [])
        rows += from_poetry(
            pyproject.get("tool", {}).get("poetry", {}).get("dependencies")
            or {}
        )

    Path(out).write_text("\n".join(rows) + "\n")


if __name__ == "__main__":
    main(sys.argv[1])
