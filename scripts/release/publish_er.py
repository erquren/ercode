#!/usr/bin/env python3
"""Collect verified native packages from release runs and publish them together."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

TARGETS = {
    "linux-x86_64-musl": "x86_64-unknown-linux-musl",
    "linux-aarch64-musl": "aarch64-unknown-linux-musl",
    "macos-aarch64": "aarch64-apple-darwin",
    "windows-x86_64": "x86_64-pc-windows-msvc",
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def api(path, method="GET", data=None):
    url = path if path.startswith("https://") else "https://api.github.com" + path
    assert urllib.parse.urlsplit(url).hostname in {"api.github.com", "uploads.github.com"}
    headers = {"Authorization": "Bearer " + os.environ["GH_TOKEN"], "Accept": "application/vnd.github+json"}
    if isinstance(data, Path):
        headers["Content-Type"] = "application/octet-stream"
        body = data.read_bytes()
    elif data is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(data).encode()
    else:
        body = None
    with urllib.request.urlopen(urllib.request.Request(url, data=body, method=method, headers=headers), timeout=600) as response:
        content = response.read()
        return json.loads(content) if content else None


def download(path, destination, accept="application/vnd.github+json"):
    request = urllib.request.Request("https://api.github.com" + path, headers={
        "Authorization": "Bearer " + os.environ["GH_TOKEN"], "Accept": accept,
    })
    try:
        response = urllib.request.build_opener(NoRedirect).open(request, timeout=120)
    except urllib.error.HTTPError as error:
        if error.code != 302:
            raise
        location = error.headers["Location"]
        assert urllib.parse.urlsplit(location).scheme == "https"
        # Signed storage URLs do not need, and must not receive, the GitHub token.
        response = urllib.request.urlopen(location, timeout=600)
    with response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def tag_commit(repo, tag):
    ref = api(f"/repos/{repo}/git/ref/tags/{tag}")["object"]
    if ref["type"] == "tag":
        ref = api(f"/repos/{repo}/git/tags/{ref['sha']}")["object"]
    assert ref["type"] == "commit", ref
    return ref["sha"]


def wait_for_builds(repo, runs, tag):
    previous = None
    while True:
        selected = {}
        for run in runs:
            for job in api(f"/repos/{repo}/actions/runs/{run}/jobs?per_page=100")["jobs"]:
                platform = job["name"].removeprefix("Build ")
                if platform in TARGETS and job["id"] > selected.get(platform, (0,))[0]:
                    selected[platform] = (job["id"], run, job)
        status = {platform: (value[2]["status"], value[2]["conclusion"]) for platform, value in selected.items()}
        if status != previous:
            print("Build status:", json.dumps(status), flush=True)
            previous = status
        if len(selected) != len(TARGETS):
            raise RuntimeError("Selected workflow runs do not contain all four build jobs")
        for platform, (_, _, job) in selected.items():
            if job["status"] == "completed" and job["conclusion"] != "success":
                raise RuntimeError(f"{platform} failed: {job['html_url']}")
        if all(value[2]["conclusion"] == "success" for value in selected.values()):
            return {platform: value[1] for platform, value in selected.items()}
        time.sleep(30)


def collect(repo, builds, tag, commit, root):
    files = []
    for platform, target in TARGETS.items():
        run = builds[platform]
        artifacts = api(f"/repos/{repo}/actions/runs/{run}/artifacts?per_page=100")["artifacts"]
        artifact = next(item for item in artifacts if item["name"] == f"er-{tag}-{platform}" and not item["expired"])
        outer = root / f"artifact-{artifact['id']}.zip"
        download(f"/repos/{repo}/actions/artifacts/{artifact['id']}/zip", outer)
        filename = f"er-{tag}-{platform}" + (".zip" if platform.startswith("windows") else ".tar.gz")
        expected = {filename, filename + ".sha256"}
        with zipfile.ZipFile(outer) as bundle:
            assert set(bundle.namelist()) == expected, bundle.namelist()
            for name in expected:
                with bundle.open(name) as source, (root / name).open("wb") as output:
                    shutil.copyfileobj(source, output)
        archive, checksum = root / filename, root / (filename + ".sha256")
        recorded, name = checksum.read_text().split()
        assert name == filename and digest(archive) == recorded, filename
        metadata_name = f"er-{tag}-{platform}/BUILD-INFO.json"
        if platform.startswith("windows"):
            with zipfile.ZipFile(archive) as bundle:
                metadata = json.loads(bundle.read(metadata_name))
        else:
            with tarfile.open(archive) as bundle:
                metadata = json.load(bundle.extractfile(metadata_name))
        assert metadata["sourceCommit"] == commit and metadata["target"] == target and metadata["version"] == tag, metadata
        assert metadata["reusedBinaries"] is False, "Every release platform must be compiled on a runner"
        print("Verified:", platform, recorded, flush=True)
        files.extend([archive, checksum])
    return files


def verify_uploaded(repo, asset, path, root):
    assert asset["size"] == path.stat().st_size, path.name
    expected = "sha256:" + digest(path)
    if asset.get("digest"):
        assert asset["digest"] == expected, (path.name, asset["digest"], expected)
    else:
        copy = root / f"verify-{asset['id']}"
        download(f"/repos/{repo}/releases/assets/{asset['id']}", copy, "application/octet-stream")
        assert digest(copy) == digest(path), path.name
        copy.unlink()


def publish(repo, tag, commit, files, root):
    assert tag_commit(repo, tag) == commit, "Release tag moved while builds were running"
    release = next((item for item in api(f"/repos/{repo}/releases") if item["tag_name"] == tag), None)
    if release is None:
        release = api(f"/repos/{repo}/releases", "POST", {"tag_name": tag, "name": "er " + tag, "draft": True})
    for path in files:
        existing = next((asset for asset in release["assets"] if asset["name"] == path.name), None)
        if existing and existing.get("digest") == "sha256:" + digest(path):
            verify_uploaded(repo, existing, path, root)
            continue
        if existing:
            api(f"/repos/{repo}/releases/assets/{existing['id']}", "DELETE")
        print("Uploading:", path.name, flush=True)
        asset = api(release["upload_url"].split("{")[0] + "?name=" + path.name, "POST", path)
        verify_uploaded(repo, asset, path, root)
    body = f'''四个平台均在 GitHub 原生构建机上从 `{tag}` 标签源码编译，源码提交为 `{commit}`。

- Windows x86_64：ZIP 包。
- Linux x86_64 / ARM64：MUSL 静态 tar.gz 包，不依赖系统 GLIBC。
- macOS ARM64 / Apple Silicon：tar.gz 包，本地临时签名，未做 Apple Developer ID 公证。

每个包包含 er、私有辅助程序、安装脚本、许可证和 BUILD-INFO.json，并附有 SHA-256 校验文件。

Linux/macOS：解压后进入目录运行 `python3 install.py .`。
Windows：解压后进入目录运行 `powershell -NoProfile -ExecutionPolicy Bypass -File .\\install.ps1`。

将 `~/.local/bin`（Windows 为 `%USERPROFILE%\\.local\\bin`）加入 PATH。配置和辅助程序保存在 `~/.ercode`，已有 Codex 安装不受影响。
从旧的 0.166.0 更新后，先用 `er --no-daemon` 启动，确保使用新后端。

本版本包括实时思考文本速度估算，以及结束行独立的输入、输出平均速率。结束速率采用模型请求累计耗时，排除请求之间的编译和工具等待。
'''
    api(f"/repos/{repo}/releases/{release['id']}", "PATCH", {
        "name": "er " + tag, "body": body, "draft": False, "prerelease": False, "make_latest": "true",
    })
    result = api(f"/repos/{repo}/releases/{release['id']}")
    assert not result["draft"]
    for path in files:
        verify_uploaded(repo, next(asset for asset in result["assets"] if asset["name"] == path.name), path, root)
    print("Published:", result["html_url"], flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"), required="GITHUB_REPOSITORY" not in os.environ)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--runs", required=True, help="Comma-separated workflow run IDs, including platform retries")
    args = parser.parse_args()
    assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?", args.tag), args.tag
    runs = [int(value.strip()) for value in args.runs.split(",")]
    commit = tag_commit(args.repo, args.tag)
    builds = wait_for_builds(args.repo, runs, args.tag)
    with tempfile.TemporaryDirectory(prefix="er-release-") as directory:
        root = Path(directory)
        files = collect(args.repo, builds, args.tag, commit, root)
        publish(args.repo, args.tag, commit, files, root)


if __name__ == "__main__":
    main()
