<!-- Modified for the ercode fork: build, package and configuration instructions. -->

# Installing and building ercode CLI

This fork provides a terminal application, not the official Codex desktop app or
cloud service. There is no separate published ercode installer in this branch.

## Prerequisites

- Git, Python 3.11 or newer, and Rust installed through the official Rust toolchain.
- The exact toolchain in `codex-rs/rust-toolchain.toml`. `rustup` selects it when
  commands run inside `codex-rs`.
- A native C/C++ compiler and platform SDK. Native Windows builds use the MSVC
  toolchain and Windows SDK; Linux builds need the usual native build tools.
- Adequate disk space for the Rust workspace, dependencies and build artifacts.
  A complete native build is substantially larger than the final CLI executable.

The package builder also fetches pinned platform resources and may fetch the
matching V8 build artifacts. Cross-compilation requires the corresponding target,
linker and SDK; passing `--target` does not install these prerequisites.

## Development build

```sh
git clone https://github.com/erquren/ercode.git
cd ercode
git switch feature/ercode-cli-brand
cd codex-rs
cargo build --locked -p codex-cli --bin ercode
cargo run --locked -p codex-cli --bin ercode -- --help
```

Use `target/debug/ercode` on Linux/macOS or `target\debug\ercode.exe` on Windows.
This builds the main CLI only. Features requiring companion executables need the
complete package described below. The `codex` binary target is retained for
upstream build compatibility; the public command for this fork is `ercode`.

## Complete package

From the repository root, use the ercode wrapper around the upstream canonical
package builder. It builds the CLI compatibility target and required companion
executables, then adds a byte-identical `ercode` executable copy. It keeps the
canonical `codex` entrypoint and metadata intact so daemon installation and
resource discovery remain compatible. Both executables use ercode branding.

Linux x86_64 with glibc:

```sh
python3 scripts/build_ercode.py --target x86_64-unknown-linux-gnu \
  --cargo-profile release --package-dir dist/ercode-linux \
  --archive-output dist/ercode-linux.tar.gz
./dist/ercode-linux/bin/ercode --help
```

Native Windows x86_64, in PowerShell:

```powershell
python scripts/build_ercode.py --target x86_64-pc-windows-msvc --cargo-profile release --package-dir dist/ercode-windows --archive-output dist/ercode-windows.zip
.\dist\ercode-windows\bin\ercode.exe --help
```

macOS Apple Silicon:

```sh
python3 scripts/build_ercode.py --target aarch64-apple-darwin \
  --cargo-profile release --package-dir dist/ercode-macos \
  --archive-output dist/ercode-macos.tar.gz
./dist/ercode-macos/bin/ercode --help
```

These are build instructions, not a claim that every platform has been tested.
Run `python3 scripts/build_ercode.py --help` for all options. A nonempty output
folder is refused by default. Use a new output path for each build; `--force`
explicitly replaces the chosen package directory and archives.

Keep the whole package together:

```text
codex-package.json
LICENSE
NOTICE
licenses/                       additional repository notices
bin/
  ercode[.exe]
  codex[.exe]                    internal compatibility copy
  codex-code-mode-host[.exe]
codex-resources/                 platform-specific sandbox helpers
codex-path/
  rg[.exe]
```

Run the packaged `ercode` executable by its full path. Adding this package's
`bin` directory to `PATH` also exposes its compatibility `codex` executable and
may shadow an official Codex installation. Keep both entrypoints and all resources
together; do not rename or delete the compatibility copy or companion executables.

The original packaging modules remain unchanged. Their detailed prerequisites,
resource verification, V8 overrides and signing/stripping rules are documented in
[scripts/codex_package/README.md](../scripts/codex_package/README.md). Do not use
upstream Codex installers to update an ercode installation.

## Configuration and authentication

ercode defaults to `~/.ercode`, keeping its user configuration and authentication
separate from official Codex's default `~/.codex`. No existing authentication or
configuration is copied automatically. First-run setup asks for API access rather
than assuming a ChatGPT subscription login.

Home selection uses the first applicable choice:

1. A nonempty `ERCODE_HOME`.
2. A nonempty, explicitly configured `CODEX_HOME` (compatibility override).
3. `~/.ercode`.

An explicit override must be an existing directory. If your shell already exports
`CODEX_HOME` for official Codex, unset it or explicitly set `ERCODE_HOME` to avoid
sharing that old home. An invalid `ERCODE_HOME` is an error, not permission to
fall back to another home. Project-local `.codex` files and internal protocol
field names are not renamed.

For a custom home on Linux/macOS:

```sh
mkdir -p "$HOME/.ercode-work"
ERCODE_HOME="$HOME/.ercode-work" /path/to/package/bin/ercode
```

For a custom home in PowerShell:

```powershell
$env:ERCODE_HOME = Join-Path $HOME '.ercode-work'
New-Item -ItemType Directory -Force $env:ERCODE_HOME | Out-Null
& 'C:\path\to\package\bin\ercode.exe'
```

The first-run wizard asks for a **Responses-compatible** API Base URL, a model ID,
and a masked API key. Chat Completions-only providers are not supported. HTTPS is
recommended. HTTP is allowed for loopback/private/link-local IPs and syntactically
local hostnames (single-label names or `.local`, `.lan`, `.internal`, `.home.arpa`).
Hostnames are not DNS-checked: use only an address on a network you trust. Before
saving any HTTP endpoint, you must type `TRUST` to acknowledge that **your API key
and code are sent in plaintext**. Changing the endpoint clears this acknowledgement.
Public HTTP endpoints are rejected. No connection/key/model test is made,
and automatic API model discovery is disabled; your first requested operation may
incur provider charges.

Setup requires a signed-out home. If existing authentication or host-managed
credentials are active, sign out explicitly or choose a separate `ERCODE_HOME`
and remove the relevant auth environment/configuration. Setup does not silently
remove or migrate credentials. Higher-priority provider/model overrides must be
removed before setup can save a key.

The endpoint is saved and verified locally before the key is stored. Keys use the
configured credential backend, never provider config: the upstream default is a
private `auth.json` file (not encrypted); `cli_auth_credentials_store = "auto"`
uses the supported OS credential backend when available, with file fallback.
Never commit a real key to this repository.
Provider authentication, account entitlements, supported API features and fees
still apply; a custom URL does not guarantee provider compatibility.

## Tests and diagnostics

The packaging wrapper has tests that require no Rust compilation or model access:

```sh
python3 scripts/test_build_ercode.py
```

For Rust changes, use the repository's existing formatting and focused test
workflows. For example, from `codex-rs`:

```sh
cargo fmt --all -- --check
cargo test --locked -p codex-cli
```

Do not mistake focused checks for a full workspace or platform validation.
`RUST_LOG` remains supported. The log filename `codex-tui.log` is an upstream
compatibility name:

```sh
ercode -c log_dir=./.ercode-log
tail -F ./.ercode-log/codex-tui.log
```
