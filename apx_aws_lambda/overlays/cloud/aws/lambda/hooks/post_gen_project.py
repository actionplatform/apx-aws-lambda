"""Cloud overlay post-gen: keep files for this language/type, and the selected CI.

`_lang/<language>/`  files that depend on the language (Dockerfile, lambda handler)
"""

import shutil
from pathlib import Path

LANGUAGE = "{{ cookiecutter.language }}"
CI = "{{ cookiecutter.ci }}"


def promote(root: Path, key: str) -> None:
    if not root.exists():
        return
    chosen = root / key
    if not chosen.exists():
        raise SystemExit(f"cloud overlay has no files under {root.name}/{key}")
    for item in chosen.rglob("*"):
        if item.is_file():
            dest = Path(*item.relative_to(chosen).parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest)
    shutil.rmtree(root)


promote(Path("_lang"), LANGUAGE)

if CI != "github" and Path(".github").exists():
    shutil.rmtree(".github")
