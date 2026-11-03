#!/usr/bin/env python3
"""ercode fork: validate release tags and build complete, checked archives."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import tomllib

from build_ercode import main as build_package
from codex_package.archive import write_archive

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "third_party/voice"))
from assemble_package import assemble as assemble_voice
from package_runtime import runtime_files

TARGETS = {
    "x86_64-unknown-linux-musl": "tar.gz",
    "x86_64-pc-windows-msvc": "zip",
    "aarch64-apple-darwin": "tar.gz",
    "x86_64-apple-darwin": "tar.gz",
}
SEMVER = r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"


def release_version(tag: str, manifest: Path = ROOT / "codex-rs/Cargo.toml") -> str:
    match = re.fullmatch(r"ercode-v" + SEMVER, tag)
    if not match:
        raise ValueError("Expected ercode-vMAJOR.MINOR.PATCH[-PRERELEASE]")
    if match[4] and any(part.isdigit() and len(part) > 1 and part.startswith("0") for part in match[4].split(".")):
        raise ValueError("Numeric prerelease identifiers must not contain leading zeroes")
    version = tag.removeprefix("ercode-v")
    workspace_version = tomllib.loads(manifest.read_text(encoding="utf-8"))["workspace"]["package"]["version"]
    if version != workspace_version:
        raise ValueError(f"Tag version {version} differs from workspace version {workspace_version}; update Cargo.toml and Cargo.lock before tagging")
    return version


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def validate_package(package: Path, target: str, version: str, bwrap_digest: str | None = None) -> None:
    windows = target.endswith("windows-msvc")
    suffix = ".exe" if windows else ""
    metadata = json.loads((package / "codex-package.json").read_text(encoding="utf-8"))
    for field, expected in {"variant": "codex", "entrypoint": f"bin/er{suffix}", "target": target, "version": version}.items():
        if metadata.get(field) != expected:
            raise ValueError(f"Incorrect package {field}: {metadata.get(field)!r}")
    required = ["LICENSE", "NOTICE", "codex-package.json", f"bin/er{suffix}", f"bin/codex-code-mode-host{suffix}", f"bin/codex-exec-server{suffix}", f"codex-path/rg{suffix}"]
    if windows:
        required += ["codex-resources/codex-command-runner.exe", "codex-resources/codex-windows-sandbox-setup.exe"]
    elif "linux" in target:
        required += ["codex-resources/bwrap"]
        if not bwrap_digest or digest(package / "codex-resources/bwrap") != bwrap_digest:
            raise ValueError("Packaged bwrap does not match the digest embedded in the CLI")
    for relative in required:
        if not (package / relative).is_file():
            raise ValueError(f"Missing package file: {relative}")
    for name in ("codex", "codex.exe", "ercode", "ercode.exe"):
        if (package / "bin" / name).exists():
            raise ValueError(f"Unexpected legacy command alias: {name}")


def validate_voice(package: Path, target: str, version: str, commit: str) -> None:
    """Verify upstream runtime inventory and every same-build package hash."""
    root = package / "codex-resources/voice"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    voice_target = target.replace("-musl", "-gnu")
    for field, value in {"schemaVersion": 1, "buildCommit": commit, "appTarget": target, "voiceTarget": voice_target, "appVersion": version}.items():
        if manifest.get(field) != value:
            raise ValueError(f"Incorrect voice manifest {field}")
    receipt = json.loads((root / "runtime.json").read_text(encoding="utf-8"))
    if receipt.get("sourceCommit") != commit:
        raise ValueError("Voice runtime was built from another commit")
    suffix = ".exe" if target.endswith("msvc") else ""
    required = {f"bin/er{suffix}", f"codex-resources/voice/bin/codex-voice-host{suffix}"}
    required.update(f"codex-resources/voice/{name}" for name in runtime_files(root, voice_target, public_release=True))
    hashes = manifest.get("sha256", {})
    if not required.issubset(hashes):
        raise ValueError("Voice manifest omits required package files")
    for relative, expected in hashes.items():
        path = package / relative
        if path.is_symlink() or not path.resolve().is_relative_to(package.resolve()) or not path.is_file():
            raise ValueError(f"Invalid voice manifest path: {relative}")
        if digest(path) != expected:
            raise ValueError(f"Voice manifest digest mismatch: {relative}")


def smoke_voice(helper: Path, commit: str) -> None:
    """Load packaged plugins without opening audio devices or making a connection."""
    actual = subprocess.check_output([str(helper), "--build-commit"], text=True, timeout=30).strip()
    if actual != commit:
        raise ValueError("Voice helper does not match the application build commit")
    env = dict(os.environ)
    env.update({name: "" for name in ("GST_PLUGIN_PATH", "GST_PLUGIN_PATH_1_0", "GST_PLUGIN_SYSTEM_PATH", "GST_PLUGIN_SYSTEM_PATH_1_0")})
    env.update(GST_REGISTRY="NUL" if os.name == "nt" else "/dev/null", GST_REGISTRY_UPDATE="no", GST_REGISTRY_FORK="no")
    with subprocess.Popen([str(helper)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env) as process:
        responses = queue.Queue()

        def read_responses():
            try:
                while True:
                    header = process.stdout.read(4)
                    if len(header) != 4:
                        raise ValueError("Voice helper closed before its reply")
                    size = int.from_bytes(header, "big")
                    if not 0 < size <= 1024 * 1024:
                        raise ValueError("Invalid voice protocol frame size")
                    responses.put(json.loads(process.stdout.read(size)))
            except BaseException as error:
                responses.put(error)

        reader = threading.Thread(target=read_responses, daemon=True)
        reader.start()
        try:
            for request, expected in (({"type": "hello", "protocol": 1, "buildCommit": commit}, "ready"), ({"type": "initializeRuntime"}, "runtimeReady"), ({"type": "close"}, "closed")):
                payload = json.dumps(request).encode()
                process.stdin.write(len(payload).to_bytes(4, "big") + payload)
                process.stdin.flush()
                response = responses.get(timeout=30)
                if response != {"type": expected}:
                    raise ValueError(f"Voice helper failed {expected}: {response}")
            process.stdin.close()
            if process.wait(timeout=30) != 0:
                raise ValueError("Voice helper exited unsuccessfully")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            reader.join(timeout=5)


def build(target: str, version: str, output: Path, voice_release_dir: Path) -> Path:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    target_root = Path(os.environ.get("CARGO_TARGET_DIR", ROOT / "codex-rs/target")).resolve()
    binaries = target_root / target / "release"
    suffix = ".exe" if target.endswith("windows-msvc") else ""
    env = dict(os.environ, CARGO_TARGET_DIR=str(target_root))
    env["STABLE_GIT_COMMIT"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    command = ["cargo", "build", "--locked", "--release", "--target", target]
    resources = []
    bwrap_digest = None
    if "linux" in target:
        subprocess.run([*command, "--bin", "bwrap"], cwd=ROOT / "codex-rs", env=env, check=True)
        bwrap = binaries / "bwrap"
        subprocess.run(["strip", "--strip-debug", "--strip-unneeded", str(bwrap)], check=True)
        bwrap_digest = digest(bwrap)
        env["CODEX_BWRAP_SHA256"] = bwrap_digest
        resources = ["--bwrap-bin", str(bwrap)]
    names = ["er", "codex-code-mode-host", "codex-exec-server"]
    if suffix:
        env["LIBSQLITE3_FLAGS"] = "SQLITE_DISABLE_INTRINSIC"
        names += ["codex-command-runner", "codex-windows-sandbox-setup"]
        for name in names[-2:]:
            resources += [f"--{name}-bin", str(binaries / f"{name}.exe")]
    args = list(command)
    for name in names:
        args += ["--bin", name]
    subprocess.run(args, cwd=ROOT / "codex-rs", env=env, check=True)
    stem = f"ercode-{version}-{target}"
    base_package = output / f"{stem}-base"
    package = output / stem
    archive = output / f"{stem}.{TARGETS[target]}"
    build_package([
        "--target", target, "--package-version", version,
        "--cargo-profile", "release", "--strip", "all",
        "--entrypoint-bin", str(binaries / f"er{suffix}"),
        "--code-mode-host-bin", str(binaries / f"codex-code-mode-host{suffix}"),
        "--exec-server-bin", str(binaries / f"codex-exec-server{suffix}"),
        "--package-dir", str(base_package), *resources,
    ])
    assemble_voice(
        base_package, voice_release_dir / f"codex-voice-host{suffix}",
        target.replace("-musl", "-gnu"), env["STABLE_GIT_COMMIT"], package,
        runtime=voice_release_dir / "runtime", release_version=version, entrypoint_name="er",
    )
    validate_package(package, target, version, bwrap_digest)
    validate_voice(package, target, version, env["STABLE_GIT_COMMIT"])
    write_archive(package, archive, force=False)
    # Check the actual downloaded form, not just the staging directory.
    extracted = output / f"{stem}-smoke"
    shutil.unpack_archive(str(archive), str(extracted))
    validate_package(extracted, target, version, bwrap_digest)
    validate_voice(extracted, target, version, env["STABLE_GIT_COMMIT"])
    subprocess.run([str(extracted / f"bin/er{suffix}"), "--version"], check=True)
    subprocess.run([str(extracted / f"codex-path/rg{suffix}"), "--version"], check=True)
    smoke_voice(extracted / f"codex-resources/voice/bin/codex-voice-host{suffix}", env["STABLE_GIT_COMMIT"])
    (output / f"{archive.name}.sha256").write_text(f"{digest(archive)}  {archive.name}\n", encoding="utf-8")
    return archive


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["validate-tag", "build"])
    parser.add_argument("--tag", required=True)
    parser.add_argument("--target", choices=TARGETS)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--voice-release-dir", type=Path)
    args = parser.parse_args()
    version = release_version(args.tag)
    if args.command == "validate-tag":
        print(version)
    elif args.target and args.voice_release_dir:
        print(build(args.target, version, args.output, args.voice_release_dir.resolve()))
    else:
        parser.error("build requires --target and --voice-release-dir")


if __name__ == "__main__":
    main()
