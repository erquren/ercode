<!-- Modified for the ercode fork: CLI branding and source-build instructions. -->

# ercode

**ercode** is a command-line coding agent based on the open-source
[OpenAI Codex CLI](https://github.com/openai/codex). This is an independently
maintained fork, not an official OpenAI product.

This first version changes the command-line branding while keeping the upstream
agent implementation and protocol compatibility. It does not add a desktop GUI.

## Build and run

Install the Rust toolchain pinned in `codex-rs/rust-toolchain.toml`, then:

```sh
git clone https://github.com/erquren/ercode.git
cd ercode
# Until this work is merged, select the rebranding branch.
git switch feature/ercode-cli-brand
cd codex-rs
cargo build --locked -p codex-cli --bin ercode
./target/debug/ercode --help
```

On Windows, the executable is `target\debug\ercode.exe`. The command above is a
CLI development build, not a complete distributable package. For normal use,
build a package that also includes the code-mode host and platform resources:

```sh
# From the repository root, for a native Linux x86_64 build:
python3 scripts/build_ercode.py --target x86_64-unknown-linux-gnu \
  --cargo-profile release --package-dir dist/ercode-linux \
  --archive-output dist/ercode-linux.tar.gz
./dist/ercode-linux/bin/ercode --help
```

See [installing and building](docs/install.md) for Windows, prerequisites and
package details. No ercode npm package, installer or prebuilt release is promised
by this branch. The upstream `@openai/codex` package installs official Codex,
not this fork.

## Compatibility

- User configuration and authentication default to `~/.ercode`, separate from
  official Codex. Home precedence is nonempty `ERCODE_HOME`, then an explicitly
  set compatibility `CODEX_HOME`, then `~/.ercode`. No old credentials are copied.
  Unset `CODEX_HOME` if your shell points it at an official Codex directory.
- First-run setup is API-oriented: configure your API base URL and API key.
  This fork does not include model weights or free service access; provider
  accounts, charges and terms still apply.
- Internal crate names, resource names and API identifiers may still say
  `codex`. They are retained deliberately for compatibility, not as product
  branding. Project-local `.codex` conventions remain compatible.
- Official Codex self-update paths are disabled for this fork. Update ercode
  from this repository and rebuild it instead.

## License and attribution

The upstream code is licensed under [Apache-2.0](LICENSE). Preserve the upstream
copyright and attribution information in [NOTICE](NOTICE), together with
applicable third-party licenses, when redistributing it. Modified source files
identify ercode fork changes. The package helper includes the repository's
license and notice files; distributors remain responsible for reviewing licenses
of the exact dependencies and resources they ship.
