#!/usr/bin/env python3
"""Build and seal same-commit upstream voice resources without remote services.

Bazel action concurrency is bounded to two. The upstream native recipe retains
its own eight-way compiler parallelism. Run before configuring musl Cargo tools:
Linux voice is a GNU/glibc helper even when the CLI package targets musl.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    "x86_64-unknown-linux-musl": ("x86_64-unknown-linux-gnu", "linux_x86_64"),
    "x86_64-pc-windows-msvc": ("x86_64-pc-windows-msvc", "windows_x86_64"),
    "aarch64-apple-darwin": ("aarch64-apple-darwin", "macos_aarch64"),
    "x86_64-apple-darwin": ("x86_64-apple-darwin", "macos_x86_64"),
}


def download_pinned(url: str, path: Path, pin: dict) -> None:
    """Download bounded public inputs and verify before using any content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    try:
        size = 0
        checksum = hashlib.sha256()
        with urllib.request.urlopen(url, timeout=120) as source, temporary.open("wb") as output:
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > pin["bytes"]:
                    raise ValueError(f"Input exceeds pinned size: {path.name}")
                checksum.update(chunk)
                output.write(chunk)
        if size != pin["bytes"] or checksum.hexdigest() != pin["sha256"]:
            raise ValueError(f"Input digest or size mismatch: {path.name}")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def windows_inputs(directory: Path) -> Path:
    manifest = json.loads((ROOT / ".github/scripts/voice-cygwin-snapshot.json").read_text())
    base = "https://github.com/openai/codex/releases/download/" + manifest["archive"]["tag"]
    for pin in (manifest["archive"], manifest["sourceArchive"]):
        download_pinned(f"{base}/{pin['name']}", directory / pin["name"], pin)
    return (directory / manifest["archive"]["name"]).resolve()


def bazel_command(target: str, root: Path, env: dict[str, str]) -> list[str]:
    # Ignore machine/user rc files, retain checked-in toolchains and release flags.
    # Fresh checkout has no user.bazelrc; refuse one rather than inherit overrides.
    if (ROOT / "user.bazelrc").exists():
        raise ValueError("Voice release builds require a checkout without user.bazelrc")
    command = [
        "bazel", "--nosystem_rc", "--nohome_rc",
        f"--output_user_root={root / 'u'}", f"--output_base={root / 'o'}",
        "--noexperimental_remote_repo_contents_cache", "build", "--config=release",
        "--jobs=2", "--spawn_strategy=local",
        "--strategy=VoiceNativePrefix=local", "--strategy=VoiceNativeRuntime=local",
        "--strategy=CcConfigureMakeRule=local",
        "--remote_cache=", "--remote_executor=", "--bes_backend=",
        "--experimental_remote_downloader=", "--disk_cache=",
        f"--repository_cache={root / 'r'}", f"--repo_contents_cache={root / 'c'}",
        "--verbose_failures",
    ]
    if target.endswith("apple-darwin"):
        command.append("--macos_minimum_os=14.0")
    if target.endswith("windows-msvc"):
        # Python uppercases os.environ keys on Windows; preserve canonical flag names.
        env = {**env, **{name: env.get(name, env.get(name.upper(), "")) for name in (
            "SystemRoot", "UCRTVersion", "UniversalCRTSdkDir", "VCINSTALLDIR",
            "VCToolsInstallDir", "WindowsLibPath", "WindowsSdkBinPath",
            "WindowsSdkDir", "WindowsSDKLibVersion", "WindowsSDKVersion",
        )}}
        for name in ("VOICE_WINDOWS_BAZEL_REPOSITORY", "CODEX_BAZEL_WINDOWS_PATH", "SystemRoot", "PROCESSOR_ARCHITECTURE"):
            if not env.get(name):
                raise ValueError(f"Missing verified Windows build environment: {name}")
        command += [
            "--config=ci-windows-msvc",
            "--host_platform=//:local_windows_msvc", "--platforms=//:local_windows_msvc",
            "--workspace_status_command=./scripts/workspace-status.cmd",
            "--repo_env=BAZEL_MSVC_RUNTIME_VISUAL_STUDIO_EULA=1",
            "--extra_toolchains=//third_party/voice:windows_pkg_config_toolchain,//third_party/voice:windows_cmake_toolchain",
            f"--inject_repository=voice_windows_tools={env['VOICE_WINDOWS_BAZEL_REPOSITORY']}",
            "--//third_party/voice:windows_installed_tools=@voice_windows_tools//:tools",
        ]
        for name in ("INCLUDE", "LIB", "LIBPATH", "UCRTVersion", "UniversalCRTSdkDir", "VCINSTALLDIR", "VCToolsInstallDir", "WindowsLibPath", "WindowsSdkBinPath", "WindowsSdkDir", "WindowsSDKLibVersion", "WindowsSDKVersion", "SystemRoot", "PROCESSOR_ARCHITECTURE"):
            if env.get(name):
                command += [f"--action_env={name}={env[name]}", f"--host_action_env={name}={env[name]}"]
        command += [f"--action_env=PATH={env['CODEX_BAZEL_WINDOWS_PATH']}", f"--host_action_env=PATH={env['CODEX_BAZEL_WINDOWS_PATH']}"]
    return [*command, "//codex-rs/voice-host:codex-voice-host", "//third_party/voice:native_runtime"]


def build(target: str, output: Path) -> None:
    voice_target, prefix = TARGETS[target]
    output = output.resolve()
    if output.exists():
        raise ValueError(f"Voice output must be fresh: {output}")
    env = dict(os.environ)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    env["STABLE_GIT_COMMIT"] = commit
    # No BuildBuddy wrapper, configuration, token, or external execution/cache.
    root = Path(env["ERCODE_BAZEL_ROOT"]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(bazel_command(target, root, env), cwd=ROOT, env=env, check=True)
    source = ROOT / "bazel-bin/third_party/voice" / f"native_runtime_{prefix}"
    receipt = json.loads((source / "runtime.json").read_text())
    if receipt.get("sourceCommit") != commit or receipt.get("target") != voice_target:
        raise ValueError("Voice build receipt does not match this commit and target")
    output.mkdir(parents=True)
    runtime = output / "runtime"
    helper_name = "codex-voice-host" + (".exe" if target.endswith("windows-msvc") else "")
    helper = output / helper_name
    commands = [sys.executable, str(ROOT / "third_party/voice/release_runtime.py")]
    subprocess.run([*commands, "stage", "--target", voice_target, "--source", str(source), "--output", str(runtime)], cwd=ROOT, env=env, check=True)
    shutil.copy2(ROOT / "bazel-bin/codex-rs/voice-host" / helper_name, helper)
    helper.chmod(helper.stat().st_mode | 0o700)
    if target.endswith("windows-msvc"):
        subprocess.run([sys.executable, str(ROOT / "third_party/voice/windows_crt.py"), "--root", str(runtime), "--target", voice_target, "--helper", str(helper)], cwd=ROOT, env=env, check=True)
    elif target.endswith("apple-darwin"):
        # Ad-hoc signing needs no Apple identity or account, unlike notarization.
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", str(helper)], check=True)
        subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(helper)], check=True)
    subprocess.run([*commands, "seal", "--target", voice_target, "--output", str(runtime)], cwd=ROOT, env=env, check=True)
    sealed = json.loads((runtime / "runtime.json").read_text())
    if sealed.get("sourceCommit") != commit or sealed.get("developmentOnly") is not False:
        raise ValueError("Sealed runtime lost its same-commit provenance")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    download = sub.add_parser("windows-inputs")
    download.add_argument("--directory", type=Path, required=True)
    native = sub.add_parser("build")
    native.add_argument("--target", choices=TARGETS, required=True)
    native.add_argument("--output", type=Path, default=ROOT / "dist/voice-release")
    args = parser.parse_args()
    if args.command == "windows-inputs":
        print(windows_inputs(args.directory))
    else:
        build(args.target, args.output)


if __name__ == "__main__":
    main()
