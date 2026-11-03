# ercode workflows

This fork has exactly two GitHub Actions workflows. Both use GitHub-hosted
runners and retain the repository's pinned Rust/native dependencies. They do
not use upstream OpenAI credentials, self-hosted runners, npm, R2 or signing
services. Existing helper actions and resource manifests remain available.

## `ercode-ci.yml`

Runs on pull requests, every branch push and manual dispatch:

- Rust formatting for the whole workspace.
- Python packaging, voice assembly and release-helper regression tests.
- Clippy (`--all-targets`) and unit tests (`--lib --bins`) for `codex-cli`,
  `codex-core` and `codex-tui` on Linux x64, Windows x64 and macOS Apple Silicon.
- Cargo uses the committed lockfile. Test reports are retained as artifacts.
- Test builds/runs continue after a Clippy failure to report both results; the
  job still fails if Clippy fails. No lint or test failure is ignored.

The scope is deliberate: this is not the upstream full workspace, integration,
voice SDK or Bazel test matrix. Their source dependencies are not removed.
PRs receive a read-only token and never run a publishing job.

## `ercode-release.yml`

Only pushes of `ercode-vMAJOR.MINOR.PATCH[-PRERELEASE]` tags in
`erquren/ercode` trigger publication. The tag version must equal
`workspace.package.version` in `codex-rs/Cargo.toml`. Update the lockfile together
with the workspace version before tagging. Ordinary branches, PRs and upstream
`rust-v*` tags cannot publish.

The workflow builds complete portable archives:

- Linux x64, musl: `.tar.gz`.
- Windows x64, MSVC: `.zip`.
- macOS Apple Silicon and Intel: one `.tar.gz` for each architecture.

Packages use the current public command `bin/er` (`bin/er.exe` on Windows),
retain the code-mode host, executor, sandbox resources and ripgrep, and include
license/notice files. They also include the same-commit voice helper and upstream
GStreamer/GLib/Opus runtime, plugins, licenses and integrity manifests. The base
package uses `scripts/codex_package`; the voice layer reuses
`third_party/voice/assemble_package.py` with the fork entrypoint `er`.
No `codex` or `ercode` command alias is installed. On Linux,
`bwrap` is built and stripped first; its final digest is embedded in the CLI
and checked against the packaged resource. V8 and other downloaded native
resources retain their upstream checksum verification.

Voice builds reuse the pinned upstream Bazel native-runtime targets and
`release_runtime.py` stage/seal checks. Windows additionally uses the public,
hash-pinned upstream Cygwin build inputs and `windows_crt.py` to bundle the retail
VC runtime. The helper, runtime receipt and CLI must share the exact commit.
Official prebuilt voice binaries are not copied into this fork.

Every archive is extracted and smoke-tested before upload: CLI/ripgrep version,
voice build-commit handshake, and loading all packaged GStreamer plugins. This
check opens no audio devices and makes no service connection, so it does not
prove microphone permissions, device operation or an authenticated voice call.
All four platforms
must succeed before the publishing job creates a draft Release, uploads the
archives plus `SHA256SUMS`, and publishes it in this repository. Prerelease tags
create prereleases. Published releases are never overwritten by a rerun. Only
the publishing job receives `contents: write`, via the automatic `GITHUB_TOKEN`.

These are unsigned portable CLI packages, not MSI/PKG installers or notarized
applications. macOS targets macOS 14 or newer. Preserve the complete extracted
folder and run its `bin/er` entrypoint. For side-by-side Unix installation, use
`scripts/install/install_er.py` from this repository; it exposes only `er` and
keeps existing Codex installations untouched. No model credentials are needed
for packaging or smoke checks. Linux has a musl CLI but a GNU/glibc voice helper,
as upstream does; the full voice package is not an Alpine/musl-only distribution.
Native system libraries/frameworks and audio devices remain OS requirements.
Linux is built and smoke-tested on Ubuntu 24.04, Windows x64 on Windows Server
2025, and macOS on macOS 15 runners. Older OS compatibility is not established
by these checks.

The release build has a larger native dependency graph than normal Rust CI.
It uses local Bazel execution without upstream cache/remote-execution credentials.
Bazel runs at most two actions concurrently; upstream native recipes retain
their own compiler parallelism. Only an actual successful tag build establishes
that all four native archives were produced and smoke-tested.

## First release

1. Merge this workflow branch after reviewing its CI results.
2. Set the desired workspace version and update `codex-rs/Cargo.lock`; commit it.
3. Confirm CI passes on that commit.
4. Create and push the matching `ercode-v...` tag when ready to publish.

Configuring these workflows does not itself create a tag or a Release. If GitHub
has disabled Actions for this fork, enable Actions in the repository before
expecting push-triggered checks. No extra secrets are required.
