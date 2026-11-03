# Portable er releases

The fork's release packages contain the `er` CLI and its required private
companions. They reuse the repository's canonical package builder. Voice is not
included.

Supported native build targets:

- Windows x86_64: ZIP.
- Linux x86_64 and ARM64: static MUSL tar.gz archives, including static ripgrep
  with PCRE2. No system GLIBC is required by the packaged executables.
- macOS Apple Silicon: tar.gz, macOS 14 or newer. Executables are ad-hoc signed,
  not Apple Developer ID signed or notarized. Other macOS architectures are not
  built by this workflow.

## Extract and run

Keep the complete extracted directory together, including `bin`, `codex-path`
and `codex-resources`. Do not copy just the main executable.

Linux/macOS:

```sh
./bin/er --version
./bin/er --no-daemon
```

Windows PowerShell:

```powershell
.\bin\er.exe --version
.\bin\er.exe --no-daemon
```

Python is not required to run these packages. The optional `install.py` script
on Linux/macOS requires Python 3.11+; `install.ps1` is optional on Windows.
They place a launcher in the user's `.local/bin` and retain the private runtime
under `.ercode`. Existing Codex installations are not replaced. Authentication
and any optional development tools used by the CLI still need user setup.

macOS may require the user to approve an unnotarized download. The workflow does
not disable Gatekeeper or change the user's security settings.

## Build or retry

Use **Actions → ercode release → Run workflow**:

1. Select the branch containing the packaging fixes.
2. Enter an existing version tag matching `workspace.package.version`, such as
   `0.166.0`.
3. Select all platforms or a single platform to retry.
4. Leave `publish` false to produce and verify artifacts without changing a
   Release. Single-platform builds never publish.

The plan resolves the source tag to one immutable commit before the matrix
starts. Each archive records the source and packaging commits in `BUILD-INFO.json`.
The four platforms may be retried using different packaging commits, but all
must use the same tagged source commit.

Pushes to `main` or `fix/**`, and PRs that change release helpers/workflows,
run package checks against that pushed/PR commit. A `release-build/<version>`
branch instead checks the existing version tag with the packaging scripts from
that branch, which is useful for repairing packaging without moving a tag.
These runs never publish and
never create tags. Their artifacts cannot be mixed into a tagged release unless
`BUILD-INFO.json` matches the tag's exact commit.

The build checks architecture and linkage, extracts the actual generated archive,
runs the CLI and companion startup checks, checks ripgrep/PCRE2 and the Linux
sandbox helper, and tests the optional installer in an isolated temporary
location. Startup checks do not establish a successful authenticated model call,
all sandbox operations, or compatibility with older operating systems.

## Publish completed builds

Use **Actions → ercode publish completed builds** with the version and
comma-separated native build run IDs. Later retries take precedence per platform.
Leave `publish` false first to download and verify all four packages without
changing a Release.

Once reviewed, enable `publish` to publish all verified files together. Existing
published releases cannot be overwritten. For an existing draft with different
files, `replace_draft_assets` must be explicitly enabled; otherwise its files are
preserved and publication stops. The same options apply to an all-platform
manual build. No workflow creates a version tag.

Transient reads/downloads have bounded retries. If a create, upload, deletion or
publication response is lost, the publisher checks the remote state before
continuing. An unresolved outcome stops for inspection rather than repeating a
possibly completed write.
