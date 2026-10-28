#!/usr/bin/env python3
"""Install a local er runtime package without exposing its helper executables."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def install(package: Path, home: Path, bin_dir: Path) -> Path:
    package = package.resolve()
    metadata = json.loads((package / "codex-package.json").read_text())
    if metadata.get("entrypoint") != "bin/er":
        raise ValueError("Expected an er package with entrypoint bin/er")
    for relative in ("bin/er", "bin/codex-code-mode-host", "bin/codex-exec-server", "codex-path/rg"):
        if not (package / relative).is_file():
            raise ValueError(f"Missing package component: {relative}")
    bin_dir.mkdir(parents=True, exist_ok=True)
    command = bin_dir / "er"
    if command.exists() and not command.is_symlink():
        raise FileExistsError(f"Refusing to overwrite existing file: {command}")
    releases = home / "packages/standalone/releases"
    releases.mkdir(parents=True, exist_ok=True)
    # A unique directory makes upgrades reversible and avoids changing a running package.
    destination = Path(tempfile.mkdtemp(prefix="er-", dir=releases))
    try:
        shutil.copytree(package, destination, dirs_exist_ok=True)
        subprocess.run([str(destination / "bin/er"), "--version"], check=True)
        temporary_link = bin_dir / f".er-{os.getpid()}"
        try:
            temporary_link.symlink_to(destination / "bin/er")
            temporary_link.replace(command)
        finally:
            temporary_link.unlink(missing_ok=True)
    except BaseException:
        shutil.rmtree(destination)
        raise
    return command


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path, help="Extracted runtime package directory")
    parser.add_argument("--home", type=Path, default=Path.home() / ".ercode")
    parser.add_argument("--bin-dir", type=Path, default=Path.home() / ".local/bin")
    args = parser.parse_args()
    print(f"Installed {install(args.package, args.home.resolve(), args.bin_dir.resolve())}")
