#!/usr/bin/env bash
set -euo pipefail

target=x86_64-unknown-linux-musl
version=0.169.0

if [[ "${CNB_BRANCH:-}" != "$version" ]]; then
  echo "Expected tag $version, got ${CNB_BRANCH:-<unset>}" >&2
  exit 1
fi

if ! command -v sudo >/dev/null; then
  apt-get update
  apt-get install -y sudo
fi
sudo apt-get update
sudo apt-get install -y ca-certificates curl xz-utils cmake ninja-build protobuf-compiler python3 binutils libssl-dev

if ! command -v rustup >/dev/null; then
  curl -fsSL --retry 3 https://sh.rustup.rs | sh -s -- -y --no-modify-path --profile minimal
fi
export PATH="$HOME/.cargo/bin:$PATH"
rustup toolchain install 1.99.1 --profile minimal --target "$target"

zig_dir="$(mktemp -d)"
zig_archive="$zig_dir/zig.tar.xz"
curl -fsSL --retry 3 https://ziglang.org/download/0.14.0/zig-linux-x86_64-0.14.0.tar.xz -o "$zig_archive"
echo "473ec26806133cf4d1918caf1a410f8403a13d979726a9045b421b685031a982  $zig_archive" | sha256sum -c -
tar -xJf "$zig_archive" -C "$zig_dir" --strip-components=1
export PATH="$zig_dir:$PATH"

export TARGET="$target"
export GITHUB_ENV="$(mktemp)"
bash .github/scripts/install-musl-build-tools.sh
while IFS='=' read -r name value; do
  export "$name=$value"
done < "$GITHUB_ENV"

export CARGO_BUILD_JOBS=16
export CARGO_INCREMENTAL=0
export CARGO_NET_GIT_FETCH_WITH_CLI=true
export CARGO_PROFILE_RELEASE_LTO=false
export CARGO_PROFILE_RELEASE_DEBUG=0
export CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16
export RUST_MIN_STACK=8388608
export AWS_LC_SYS_NO_JITTER_ENTROPY=1
export LIBSQLITE3_FLAGS=SQLITE_DISABLE_INTRINSIC

python3 scripts/release/package_er.py --source . --target "$target" --output-dir dist
