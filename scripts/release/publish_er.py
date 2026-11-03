#!/usr/bin/env python3
"""Collect verified native packages from release runs and publish them together."""

import argparse
import hashlib
import http.client
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


MAX_ATTEMPTS = 4


def transient(error):
    if isinstance(error, urllib.error.HTTPError):
        return error.code in {408, 429, 500, 502, 503, 504} or (
            error.code == 403 and error.headers.get("X-RateLimit-Remaining") == "0")
    return isinstance(error, (urllib.error.URLError, TimeoutError, ConnectionError,
                              http.client.HTTPException))


def pause(attempt, error):
    delay = min(2 ** attempt, 30)
    if isinstance(error, urllib.error.HTTPError):
        retry_after = error.headers.get("Retry-After", "")
        if retry_after.isdigit():
            delay = min(int(retry_after), 60)
    print(f"Transient request failure; retrying in {delay}s", flush=True)
    time.sleep(delay)


def retry_read(operation):
    for attempt in range(MAX_ATTEMPTS):
        try:
            return operation()
        except Exception as error:
            if not transient(error) or attempt == MAX_ATTEMPTS - 1:
                raise
            pause(attempt, error)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def api(path, method="GET", data=None):
    url = path if path.startswith("https://") else "https://api.github.com" + path
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in {"api.github.com", "uploads.github.com"}
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise ValueError("Refusing non-GitHub API destination")
    headers = {"Authorization": "Bearer " + os.environ["GH_TOKEN"], "Accept": "application/vnd.github+json"}
    if isinstance(data, Path):
        headers["Content-Type"] = "application/octet-stream"
        body = data.read_bytes()
    elif data is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(data).encode()
    else:
        body = None

    def request():
        # Never let urllib forward an authenticated request to a redirect target.
        with urllib.request.build_opener(NoRedirect).open(
                urllib.request.Request(url, data=body, method=method, headers=headers), timeout=120 if method == "GET" else 600) as response:
            content = response.read()
            return json.loads(content) if content else None
    # A failed write may already have completed. Its caller must reconcile it.
    return retry_read(request) if method == "GET" else request()


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise ValueError("Refusing insecure storage redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(path, destination, accept="application/vnd.github+json"):
    destination = Path(destination)

    def attempt():
        request = urllib.request.Request("https://api.github.com" + path, headers={
            "Authorization": "Bearer " + os.environ["GH_TOKEN"], "Accept": accept,
        })
        try:
            response = urllib.request.build_opener(NoRedirect).open(request, timeout=120)
        except urllib.error.HTTPError as error:
            if error.code not in {301, 302, 303, 307, 308}:
                raise
            location = error.headers["Location"]
            error.close()
            if urllib.parse.urlsplit(location).scheme != "https":
                raise ValueError("Refusing insecure artifact download")
            # A fresh unauthenticated request is essential for signed storage URLs.
            response = urllib.request.build_opener(HTTPSRedirect).open(location, timeout=120)
        temporary = None
        try:
            with response, tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as output:
                temporary = Path(output.name)
                shutil.copyfileobj(response, output)
                length = response.headers.get("Content-Length")
                if length is not None and output.tell() != int(length):
                    raise http.client.IncompleteRead(b"", int(length))
            temporary.replace(destination)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    retry_read(attempt)


def paginated(path, key=None):
    separator = "&" if "?" in path else "?"
    page = 1
    while True:
        result = api(f"{path}{separator}per_page=100&page={page}")
        items = result[key] if key else result
        yield from items
        if len(items) < 100:
            return
        page += 1


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def tag_commit(repo, tag):
    ref = api(f"/repos/{repo}/git/ref/tags/{tag}")["object"]
    while ref["type"] == "tag":
        ref = api(f"/repos/{repo}/git/tags/{ref['sha']}")["object"]
    assert ref["type"] == "commit", ref
    return ref["sha"]


def wait_for_builds(repo, runs, tag):
    previous = None
    while True:
        selected = {}
        for run in runs:
            for job in paginated(f"/repos/{repo}/actions/runs/{run}/jobs", "jobs"):
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
        artifacts = paginated(f"/repos/{repo}/actions/runs/{run}/artifacts", "artifacts")
        artifact = next(item for item in artifacts if item["name"] == f"er-{tag}-{platform}" and not item["expired"])
        outer = root / f"artifact-{artifact['id']}.zip"
        download(f"/repos/{repo}/actions/artifacts/{artifact['id']}/zip", outer)
        filename = f"er-{tag}-{platform}" + (".zip" if platform.startswith("windows") else ".tar.gz")
        expected = {filename, filename + ".sha256"}
        with zipfile.ZipFile(outer) as bundle:
            assert len(bundle.namelist()) == 2 and set(bundle.namelist()) == expected, bundle.namelist()
            for name in expected:
                with bundle.open(name) as source, (root / name).open("wb") as output:
                    shutil.copyfileobj(source, output)
        archive, checksum = root / filename, root / (filename + ".sha256")
        verify_package(archive, checksum, tag, commit, platform, target)
        files.extend([archive, checksum])
    return files


def verify_package(archive, checksum, tag, commit, platform, target):
    recorded, name = checksum.read_text().split()
    assert name == archive.name and digest(archive) == recorded, archive.name
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


def verify_replacement_files(files, tag, commit):
    expected = {}
    for platform, target in TARGETS.items():
        name = f"er-{tag}-{platform}" + (".zip" if platform.startswith("windows") else ".tar.gz")
        expected[name] = (platform, target)
    paths = {path.name: path for path in files}
    names = set(expected) | {name + ".sha256" for name in expected}
    if len(files) != len(names) or set(paths) != names:
        raise RuntimeError("Draft replacement requires all four verified packages and checksums")
    for name, (platform, target) in expected.items():
        verify_package(paths[name], paths[name + ".sha256"], tag, commit, platform, target)


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


def find_release(repo, tag):
    matches = [item for item in paginated(f"/repos/{repo}/releases") if item["tag_name"] == tag]
    if len(matches) > 1:
        raise RuntimeError("Multiple releases found for tag")
    return matches[0] if matches else None


def require_draft(release):
    if not release["draft"]:
        raise RuntimeError("Refusing to modify an already published release")
    return release


def fresh_draft(repo, release_id):
    return require_draft(api(f"/repos/{repo}/releases/{release_id}"))


def release_assets(repo, release_id):
    return list(paginated(f"/repos/{repo}/releases/{release_id}/assets"))


def create_draft(repo, tag, commit):
    # Reconcile any previous attempt before considering a new draft.
    existing = find_release(repo, tag)
    if existing:
        return require_draft(existing)
    assert tag_commit(repo, tag) == commit, "Release tag moved"
    try:
        return require_draft(api(f"/repos/{repo}/releases", "POST", {
            "tag_name": tag, "target_commitish": commit, "name": "er " + tag, "draft": True}))
    except Exception as error:
        if not transient(error):
            raise
        existing = find_release(repo, tag)
        if existing:
            return require_draft(existing)
        # Absence after an uncertain POST is not proof it was never applied.
        # Stop rather than risking a duplicate create; a rerun reconciles again.
        raise RuntimeError("Draft creation outcome uncertain; inspect release before rerunning") from error


def delete_draft_asset(repo, release, asset):
    fresh_draft(repo, release["id"])
    # Delete only the exact current asset identified during mismatch verification.
    current = next((item for item in release_assets(repo, release["id"])
                    if item["id"] == asset["id"]), None)
    if current is None:
        return
    if current["name"] != asset["name"]:
        raise RuntimeError("Draft asset changed before deletion")
    try:
        api(f"/repos/{repo}/releases/assets/{asset['id']}", "DELETE")
    except Exception as error:
        if not transient(error):
            raise
        fresh_draft(repo, release["id"])
        if any(item["id"] == asset["id"] for item in release_assets(repo, release["id"])):
            raise RuntimeError("Draft asset deletion outcome uncertain; inspect before rerunning") from error


def upload_asset(repo, release, path, root, replace_draft_assets=False):
    fresh = fresh_draft(repo, release["id"])
    existing = next((asset for asset in release_assets(repo, release["id"])
                     if asset["name"] == path.name), None)
    if existing:
        try:
            verify_uploaded(repo, existing, path, root)
            return
        except AssertionError:
            if not replace_draft_assets:
                raise
        delete_draft_asset(repo, release, existing)
        fresh = fresh_draft(repo, release["id"])
    url = fresh["upload_url"].split("{")[0] + "?" + urllib.parse.urlencode({"name": path.name})
    print("Uploading:", path.name, flush=True)
    try:
        asset = api(url, "POST", path)
    except Exception as error:
        if not transient(error):
            raise
        fresh_draft(repo, release["id"])
        asset = next((item for item in release_assets(repo, release["id"])
                      if item["name"] == path.name), None)
        if asset is None:
            raise RuntimeError(f"Upload outcome uncertain for {path.name}; rerun after inspection") from error
    verify_uploaded(repo, asset, path, root)


def publish(repo, tag, commit, files, root, replace_draft_assets=False):
    assert tag_commit(repo, tag) == commit, "Release tag moved while builds were running"
    if replace_draft_assets:
        verify_replacement_files(files, tag, commit)
    release = create_draft(repo, tag, commit)
    for path in files:
        upload_asset(repo, release, path, root, replace_draft_assets)
    body = f'''四个平台均在 GitHub 原生构建机上从 `{tag}` 标签源码编译，源码提交为 `{commit}`。

- Windows x86_64：ZIP 包。
- Linux x86_64 / ARM64：MUSL 静态 tar.gz 包，不依赖系统 GLIBC。
- macOS ARM64 / Apple Silicon：tar.gz 包，本地临时签名，未做 Apple Developer ID 公证。

每个包包含 er、私有辅助程序、安装脚本、许可证和 BUILD-INFO.json，并附有 SHA-256 校验文件。

解压后即可直接运行，无需 Python：
Linux/macOS：进入解压目录运行 `./bin/er`。
Windows：进入解压目录运行 `.\\bin\\er.exe`。

可选安装到用户目录：Linux/macOS 运行 `python3 install.py .`（仅安装脚本需要 Python）；
Windows 运行 `powershell -NoProfile -ExecutionPolicy Bypass -File .\\install.ps1`。

将 `~/.local/bin`（Windows 为 `%USERPROFILE%\\.local\\bin`）加入 PATH。配置和辅助程序保存在 `~/.ercode`，已有 Codex 安装不受影响。
从旧的 0.166.0 更新后，先用 `er --no-daemon` 启动，确保使用新后端。

本版本包括实时思考文本速度估算，以及结束行独立的输入、输出平均速率。结束速率采用模型请求累计耗时，排除请求之间的编译和工具等待。
'''
    payload = {
        "name": "er " + tag, "body": body, "draft": False, "prerelease": False, "make_latest": "true",
    }
    fresh_draft(repo, release["id"])
    assert tag_commit(repo, tag) == commit, "Release tag moved before publication"
    # Verify the full current asset set before making this release public.
    assets = release_assets(repo, release["id"])
    for path in files:
        verify_uploaded(repo, next(asset for asset in assets if asset["name"] == path.name), path, root)
    try:
        api(f"/repos/{repo}/releases/{release['id']}", "PATCH", payload)
    except Exception as error:
        if not transient(error):
            raise
        result = api(f"/repos/{repo}/releases/{release['id']}")
        if result["draft"]:
            raise RuntimeError("Publication outcome uncertain; inspect draft before rerunning") from error
        if any(result.get(key) != payload[key] for key in ("name", "body", "prerelease")):
            raise RuntimeError("Published release differs from requested state") from error
    result = api(f"/repos/{repo}/releases/{release['id']}")
    assert not result["draft"]
    assets = release_assets(repo, release["id"])
    for path in files:
        verify_uploaded(repo, next(asset for asset in assets if asset["name"] == path.name), path, root)
    print("Published:", result["html_url"], flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"), required="GITHUB_REPOSITORY" not in os.environ)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--runs", required=True, help="Comma-separated workflow run IDs, including platform retries")
    parser.add_argument("--check-only", action="store_true",
                        help="Download and validate packages without creating or changing a release")
    parser.add_argument("--replace-draft-assets", action="store_true",
                        help="Replace mismatched draft assets only after verifying all four native packages")
    args = parser.parse_args()
    assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?", args.tag), args.tag
    runs = [int(value.strip()) for value in args.runs.split(",")]
    commit = tag_commit(args.repo, args.tag)
    builds = wait_for_builds(args.repo, runs, args.tag)
    with tempfile.TemporaryDirectory(prefix="er-release-") as directory:
        root = Path(directory)
        files = collect(args.repo, builds, args.tag, commit, root)
        if args.check_only:
            assert tag_commit(args.repo, args.tag) == commit, "Release tag moved during validation"
            print("Validated all four native packages; no release changes made", flush=True)
        else:
            publish(args.repo, args.tag, commit, files, root, args.replace_draft_assets)


if __name__ == "__main__":
    main()
