"""Release checks that run without compilers, network, credentials or tags."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ercode_release as release


class ReleaseTest(unittest.TestCase):
    def manifest(self, root, version="1.2.3"):
        path = root / "Cargo.toml"
        path.write_text(f'[workspace.package]\nversion = "{version}"\n')
        return path

    def test_accepts_matching_stable_and_prerelease_tags(self):
        with tempfile.TemporaryDirectory() as tmp:
            for version in ("1.2.3", "0.166.0", "1.2.3-rc.1"):
                with self.subTest(version=version):
                    self.assertEqual(release.release_version(f"ercode-v{version}", self.manifest(Path(tmp), version)), version)

    def test_rejects_mismatched_and_unsafe_tags(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self.manifest(Path(tmp))
            for tag in ("rust-v1.2.3", "v1.2.3", "ercode-v1.2.4", "ercode-v01.2.3", "ercode-v1.2.3-rc.01", "ercode-v1.2.3;echo unsafe", "ercode-v1.2.3\n", "ercode-v1.2.3+metadata"):
                with self.subTest(tag=tag), self.assertRaises(ValueError):
                    release.release_version(tag, manifest)

    def fixture(self, root, target):
        suffix = ".exe" if target.endswith("msvc") else ""
        files = ["LICENSE", "NOTICE", f"bin/er{suffix}", f"bin/codex-code-mode-host{suffix}", f"bin/codex-exec-server{suffix}", f"codex-path/rg{suffix}"]
        if suffix:
            files += ["codex-resources/codex-command-runner.exe", "codex-resources/codex-windows-sandbox-setup.exe"]
        if "linux" in target:
            files += ["codex-resources/bwrap"]
        for name in files:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"inert fixture")
        (root / "codex-package.json").write_text(json.dumps({"variant": "codex", "entrypoint": f"bin/er{suffix}", "target": target, "version": "1.2.3"}))
        return release.digest(root / "codex-resources/bwrap") if "linux" in target else None

    def test_complete_package_and_missing_helper_on_every_platform(self):
        for target in release.TARGETS:
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                bwrap_digest = self.fixture(root, target)
                release.validate_package(root, target, "1.2.3", bwrap_digest)
                suffix = ".exe" if target.endswith("msvc") else ""
                (root / f"bin/codex-exec-server{suffix}").unlink()
                with self.assertRaisesRegex(ValueError, "Missing package file"):
                    release.validate_package(root, target, "1.2.3", bwrap_digest)

    def test_rejects_wrong_version_target_alias_and_bwrap_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = "x86_64-unknown-linux-musl"
            bwrap_digest = self.fixture(root, target)
            with self.assertRaisesRegex(ValueError, "version"):
                release.validate_package(root, target, "2.0.0", bwrap_digest)
            with self.assertRaisesRegex(ValueError, "target"):
                release.validate_package(root, "aarch64-apple-darwin", "1.2.3")
            with self.assertRaisesRegex(ValueError, "digest"):
                release.validate_package(root, target, "1.2.3", "wrong")
            (root / "bin/codex").write_bytes(b"alias")
            with self.assertRaisesRegex(ValueError, "alias"):
                release.validate_package(root, target, "1.2.3", bwrap_digest)

    def test_builds_locked_bwrap_before_cli_and_packages_exact_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = "x86_64-unknown-linux-musl"
            target_dir = root / "target"
            binaries = target_dir / target / "release"
            binaries.mkdir(parents=True)
            (binaries / "bwrap").write_bytes(b"stripped fixture")
            output = root / "dist"
            archive = output / f"ercode-1.2.3-{target}.tar.gz"

            def package(args):
                archive.write_bytes(b"archive fixture")

            with patch.dict(os.environ, {"CARGO_TARGET_DIR": str(target_dir)}), patch.object(release.subprocess, "check_output", return_value="a" * 40 + "\n"), patch.object(release.subprocess, "run") as run, patch.object(release, "build_package", side_effect=package) as builder, patch.object(release, "validate_package") as validate, patch.object(release.shutil, "unpack_archive"):
                self.assertEqual(release.build(target, "1.2.3", output), archive)
            calls = run.call_args_list
            self.assertIn("--locked", calls[0].args[0])
            self.assertEqual(calls[0].args[0][-2:], ["--bin", "bwrap"])
            self.assertEqual(calls[1].args[0][0], "strip")
            self.assertNotIn("bwrap", calls[2].args[0])
            self.assertIn("--locked", calls[2].args[0])
            self.assertEqual(calls[2].kwargs["env"]["CODEX_BWRAP_SHA256"], release.digest(binaries / "bwrap"))
            args = builder.call_args.args[0]
            self.assertEqual(args[args.index("--bwrap-bin") + 1], str(binaries / "bwrap"))
            for option in ("--entrypoint-bin", "--code-mode-host-bin", "--exec-server-bin"):
                self.assertIn(option, args)
            self.assertEqual(validate.call_count, 2)
            self.assertEqual((output / f"{archive.name}.sha256").read_text(), f"{release.digest(archive)}  {archive.name}\n")


if __name__ == "__main__":
    unittest.main()
