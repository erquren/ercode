# ercode workflows

This fork has exactly two GitHub Actions workflows. Both use GitHub-hosted
runners and retain the repository's pinned Rust/native dependencies. They do
not use upstream OpenAI credentials, self-hosted runners, npm, R2 or signing
services. Existing helper actions and resource manifests remain available.

## `ercode-ci.yml`

Runs on pull requests, every branch push and manual dispatch:

- Rust formatting for the whole workspace.
- Python packaging and release-helper regression tests.
- Clippy (`--all-targets`) and unit tests (`--lib --bins`) for `codex-cli`,
  `codex-core` and `codex-tui` on Linux x64, Windows x64 and macOS Apple Silicon.
- Cargo uses the committed lockfile. Test reports are retained as artifacts.

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
license/notice files. No `codex` or `ercode` command alias is installed. On Linux,
`bwrap` is built and stripped first; its final digest is embedded in the CLI
and checked against the packaged resource. V8 and other downloaded native
resources retain their upstream checksum verification.

Every archive is extracted and smoke-tested before upload. All four platforms
must succeed before the publishing job creates a draft Release, uploads the
archives plus `SHA256SUMS`, and publishes it in this repository. Prerelease tags
create prereleases. Published releases are never overwritten by a rerun. Only
the publishing job receives `contents: write`, via the automatic `GITHUB_TOKEN`.

These are unsigned portable CLI packages, not MSI/PKG installers or notarized
applications. macOS targets macOS 14 or newer. Preserve the complete extracted
folder and run its `bin/er` entrypoint. For side-by-side Unix installation, use
`scripts/install/install_er.py` from this repository; it exposes only `er` and
keeps existing Codex installations untouched. No model credentials are needed
for packaging or `--version` smoke checks. Native voice-runtime distribution is
not part of these archives, matching the existing canonical package builder.

## First release

1. Merge this workflow branch after reviewing its CI results.
2. Set the desired workspace version and update `codex-rs/Cargo.lock`; commit it.
3. Confirm CI passes on that commit.
4. Create and push the matching `ercode-v...` tag when ready to publish.

Configuring these workflows does not itself create a tag or a Release. If GitHub
has disabled Actions for this fork, enable Actions in the repository before
expecting push-triggered checks. No extra secrets are required.
