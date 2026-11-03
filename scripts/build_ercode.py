#!/usr/bin/env python3
"""ercode fork addition: assemble the CLI with upstream companion resources.

The canonical builder uses the internal ``codex`` package variant and the
public ``er`` executable. Preserve that entrypoint and metadata for daemon
package adoption while including the required redistribution notices.
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
    """Add redistribution notices without changing the canonical package layout."""
    metadata_path = package_dir / "codex-package.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    entrypoint = metadata.get("entrypoint")
    if metadata.get("variant") != "codex" or entrypoint not in (
        "bin/er",
        "bin/er.exe",
    ):
        raise ValueError("Expected a canonical codex CLI package")
    if not (package_dir / entrypoint).is_file():
        raise ValueError("Expected the canonical er CLI executable")
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
