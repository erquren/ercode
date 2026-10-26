#!/usr/bin/env python3
"""ercode fork addition: assemble the CLI with upstream companion resources.

The canonical builder still uses the internal ``codex`` package variant. An
``ercode`` executable copy exposes the public brand, while the canonical
entrypoint and metadata remain intact for daemon package adoption.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
os.environ["CODEX_REPO_ROOT"] = str(REPO_ROOT)
sys.path.insert(0, str(Path(__file__).resolve().parent))

from codex_package.cli import archive_package, assemble_package, create_parser


def brand_package(package_dir: Path, repo_root: Path = REPO_ROOT) -> None:
    """Rebrand a newly assembled package, retaining its internal layout."""
    metadata_path = package_dir / "codex-package.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    old_entrypoint = metadata.get("entrypoint")
    if metadata.get("variant") != "codex" or old_entrypoint not in (
        "bin/codex",
        "bin/codex.exe",
    ):
        raise ValueError("Expected a canonical codex CLI package")
    name = "ercode.exe" if old_entrypoint.endswith(".exe") else "ercode"
    executable = package_dir / "bin" / name
    if executable.exists():
        raise FileExistsError(f"Refusing to overwrite {executable}")
    # Never discard the upstream attribution when distributing the fork.
    for notice in ("LICENSE", "NOTICE"):
        shutil.copy2(repo_root / notice, package_dir / notice)
    for notice in (
        "third_party/wezterm/LICENSE",
        "third_party/voice/NOTICE.md",
        "codex-rs/vendor/bubblewrap/LICENSE",
    ):
        source = repo_root / notice
        if source.is_file():
            destination = package_dir / "licenses" / notice
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    # Daemon installation requires the canonical entrypoint in both the manifest
    # and package. A byte-identical copy is portable to Windows, unlike symlinks,
    # and preserves executable identity checks without changing internal paths.
    shutil.copy2(package_dir / old_entrypoint, executable)


def main(argv: list[str] | None = None) -> int:
    parser = create_parser()
    parser.description = "Build an ercode CLI package with companion resources and license notices."
    args = parser.parse_args(argv)
    if args.variant != "codex":
        parser.error("ercode packages support only the codex CLI variant")
    package_dir = assemble_package(args)
    brand_package(package_dir)
    archive_package(package_dir, args)
    print(f"ercode package: {package_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
