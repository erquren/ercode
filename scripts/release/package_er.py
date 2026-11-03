#!/usr/bin/env python3
"""Build and verify a complete er runtime archive from a specific source checkout."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile

TARGETS = {
    "x86_64-unknown-linux-musl": "linux-x86_64-musl",
    "aarch64-unknown-linux-musl": "linux-aarch64-musl",
    "aarch64-apple-darwin": "macos-aarch64",
    "x86_64-pc-windows-msvc": "windows-x86_64",
}


def run(command, **kwargs):
    print("+", " ".join(map(str, command)), flush=True)
    return subprocess.run(list(map(str, command)), check=True, **kwargs)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def git_commit(source):
    return subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()


def executables(package, windows):
    suffix = ".exe" if windows else ""
    files = [package / "bin" / (name + suffix) for name in ("er", "codex-code-mode-host", "codex-exec-server")]
    files.append(package / "codex-path" / ("rg" + suffix))
    if windows:
        files.extend(package / "codex-resources" / name for name in ("codex-command-runner.exe", "codex-windows-sandbox-setup.exe"))
    elif "linux" in json.loads((package / "codex-package.json").read_text())["target"]:
        files.append(package / "codex-resources/bwrap")
    return files


def verify_architecture(path, target):
    if "linux" in target:
        with path.open("rb") as stream:
            data = stream.read(64)
        expected = 62 if target.startswith("x86_64") else 183
        assert data[:4] == b"\x7fELF" and data[4:6] == b"\x02\x01", path
        assert struct.unpack_from("<H", data, 18)[0] == expected, path
        headers = subprocess.check_output(["readelf", "-l", str(path)], text=True)
        dynamic = subprocess.check_output(["readelf", "-d", str(path)], text=True)
        assert "INTERP" not in headers and "(NEEDED)" not in dynamic, f"Not static: {path}"
    elif "windows" in target:
        with path.open("rb") as stream:
            header = stream.read(64)
            assert header[:2] == b"MZ", path
            stream.seek(struct.unpack_from("<I", header, 60)[0])
            pe = stream.read(6)
        assert pe[:4] == b"PE\0\0" and struct.unpack_from("<H", pe, 4)[0] == 0x8664, path
    else:
        run(["lipo", "-verify_arch", "arm64", path])
        libraries = subprocess.check_output(["otool", "-L", str(path)], text=True)
        assert "/opt/homebrew/" not in libraries and "/usr/local/opt/" not in libraries, libraries


def sign_macos(package, source):
    for path in (package / "bin").iterdir():
        entitlement_name = "codex" if path.name == "er" else path.name
        entitlements = source / ".github/scripts/macos-signing" / (entitlement_name + ".entitlements.plist")
        run(["codesign", "--force", "--sign", "-", "--options", "runtime", "--entitlements", entitlements, path])
        run(["codesign", "--verify", "--strict", path])


def archive_package(package, archive):
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as out:
            for path in sorted(package.rglob("*")):
                if path.is_file():
                    out.write(path, path.relative_to(package.parent))
    else:
        with tarfile.open(archive, "w:gz", compresslevel=6) as out:
            out.add(package, arcname=package.name)
    checksum = archive.with_name(archive.name + ".sha256")
    checksum.write_text(f"{digest(archive)}  {archive.name}\n", encoding="utf-8")
    print(f"Archive: {archive} ({archive.stat().st_size} bytes)", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", choices=TARGETS, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--existing-package", type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    output = args.output_dir.resolve()
    version = tomllib.loads((source / "codex-rs/Cargo.toml").read_text())["workspace"]["package"]["version"]
    commit = git_commit(source)
    windows = "windows" in args.target
    package = output / f"er-{version}-{TARGETS[args.target]}"
    output.mkdir(parents=True, exist_ok=True)
    original_info = None
    if args.existing_package:
        existing = args.existing_package.resolve()
        original_info = json.loads((existing / "BUILD-INFO.json").read_text())
        assert original_info["sourceCommit"] == commit, "Prebuilt source commit does not match release tag"
        assert original_info["target"] == args.target, "Prebuilt target mismatch"
        shutil.copytree(existing, package)
    else:
        env = {**os.environ, "CODEX_REPO_ROOT": str(source)}
        run([sys.executable, source / "scripts/build_codex_package.py", "--target", args.target,
             "--cargo-profile", "release", "--package-dir", package], env=env)
    metadata = json.loads((package / "codex-package.json").read_text())
    assert metadata["target"] == args.target and metadata["version"] == version, metadata
    assert metadata["entrypoint"] == ("bin/er.exe" if windows else "bin/er"), metadata
    for name in ("LICENSE", "NOTICE"):
        shutil.copy2(source / name, package / name)
    for name in ("third_party/wezterm/LICENSE", "third_party/voice/NOTICE.md", "codex-rs/vendor/bubblewrap/LICENSE"):
        if (source / name).is_file():
            destination = package / "licenses" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / name, destination)
    if windows:
        shutil.copy2(Path(__file__).parent / "install_er.ps1", package / "install.ps1")
        install = "powershell -NoProfile -ExecutionPolicy Bypass -File .\\install.ps1"
        launch = r"%USERPROFILE%\.local\bin\er.cmd --no-daemon"
    else:
        shutil.copy2(source / "scripts/install/install_er.py", package / "install.py")
        install = "python3 install.py ."
        launch = "~/.local/bin/er --no-daemon"
    if (source / "docs/token-speeds.md").exists():
        shutil.copy2(source / "docs/token-speeds.md", package / "TOKEN-SPEEDS.md")
    notes = [f"er {version} - {TARGETS[args.target]}", "", "Install from this directory:",
             f"  {install}", "", "Launch the new backend after upgrading:", f"  {launch}", "",
             "Keep the full package together. Helpers remain private under ~/.ercode.",
             "Add ~/.local/bin to PATH to use the er command.", f"Source commit: {commit}"]
    if "linux" in args.target:
        notes.append("All packaged executables are static MUSL builds and require no system GLIBC.")
    if "darwin" in args.target:
        notes.append("Apple Silicon build. Ad-hoc signed for local execution; not Developer ID notarized.")
        sign_macos(package, source)
    (package / "README.txt").write_text("\n".join(notes) + "\n", encoding="utf-8")
    info = {"version": version, "sourceCommit": commit, "target": args.target, "profile": "release",
            "packagingCommit": git_commit(Path(__file__).resolve().parents[2]),
            "reusedBinaries": bool(args.existing_package)}
    if original_info:
        info["originalBuild"] = original_info
    else:
        info["buildSettings"] = {key: os.environ.get(key) for key in
                                 ("CARGO_PROFILE_RELEASE_LTO", "CARGO_PROFILE_RELEASE_DEBUG", "CARGO_PROFILE_RELEASE_CODEGEN_UNITS")}
    (package / "BUILD-INFO.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    for binary in executables(package, windows):
        assert binary.is_file(), binary
        verify_architecture(binary, args.target)
    cli = package / metadata["entrypoint"]
    banner = subprocess.check_output([str(cli), "--version"], text=True).strip()
    assert banner == f"er {version}", banner
    for name in ("er", "codex-code-mode-host", "codex-exec-server"):
        run([package / "bin" / (name + (".exe" if windows else "")), "--help"], stdout=subprocess.DEVNULL)
    with tempfile.TemporaryDirectory(prefix="er-install-check-") as temp:
        temp = Path(temp)
        if windows:
            run(["pwsh", "-NoProfile", "-File", package / "install.ps1", "-ErHome", temp / "config",
                 "-BinDir", temp / "bin"])
            run(["cmd.exe", "/d", "/c", str(temp / "bin/er.cmd"), "--version"])
        else:
            run([sys.executable, package / "install.py", package, "--home", temp / "config", "--bin-dir", temp / "bin"])
            run([temp / "bin/er", "--version"])
    archive_package(package, package.with_name(package.name + (".zip" if windows else ".tar.gz")))


if __name__ == "__main__":
    main()
