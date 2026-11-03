"""ercode fork addition: packaging regression checks without a Rust build."""

import importlib.util
import json
from pathlib import Path
import tempfile
import zipfile
import unittest
from unittest.mock import patch

MODULE_PATH = Path(__file__).with_name("build_ercode.py")
spec = importlib.util.spec_from_file_location("build_ercode", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ErCodePackageTest(unittest.TestCase):
    def fixture(self, root, windows=False):
        repo, package = root / "repo", root / "package"
        repo.mkdir()
        (package / "bin").mkdir(parents=True)
        for name in ("LICENSE", "NOTICE"):
            (repo / name).write_text(f"original {name}\n")
        license_file = repo / "third_party/wezterm/LICENSE"
        license_file.parent.mkdir(parents=True)
        license_file.write_text("third-party notice\n")
        suffix = ".exe" if windows else ""
        binary = package / "bin" / f"er{suffix}"
        binary.write_bytes(b"test executable")
        binary.chmod(0o755)
        companion = package / "bin" / f"codex-code-mode-host{suffix}"
        companion.write_bytes(b"companion")
        (package / "codex-resources").mkdir()
        metadata = {"layoutVersion": 1, "variant": "codex", "entrypoint": f"bin/er{suffix}", "resourcesDir": "codex-resources"}
        (package / "codex-package.json").write_text(json.dumps(metadata))
        return repo, package, suffix

    def test_linux_and_windows_keep_resources_and_notices(self):
        for windows in (False, True):
            with self.subTest(windows=windows), tempfile.TemporaryDirectory() as tmp:
                repo, package, suffix = self.fixture(Path(tmp), windows)
                metadata_before = (package / "codex-package.json").read_bytes()
                module.brand_package(package, repo)
                self.assertEqual((package / "codex-package.json").read_bytes(), metadata_before)
                binary = package / "bin" / f"er{suffix}"
                self.assertEqual(binary.read_bytes(), b"test executable")
                self.assertTrue(binary.stat().st_mode & 0o111)
                self.assertFalse((package / "bin" / f"codex{suffix}").exists())
                self.assertFalse((package / "bin" / f"ercode{suffix}").exists())
                self.assertEqual((package / "bin" / f"codex-code-mode-host{suffix}").read_bytes(), b"companion")
                metadata = json.loads((package / "codex-package.json").read_text())
                self.assertEqual(metadata["entrypoint"], f"bin/er{suffix}")
                self.assertEqual(metadata["variant"], "codex")
                self.assertEqual(metadata["resourcesDir"], "codex-resources")
                for name in ("LICENSE", "NOTICE"):
                    self.assertEqual((repo / name).read_bytes(), (package / name).read_bytes())
                self.assertEqual((package / "licenses/third_party/wezterm/LICENSE").read_text(), "third-party notice\n")

    def test_rejects_missing_canonical_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, package, _ = self.fixture(Path(tmp))
            (package / "bin/er").unlink()
            with self.assertRaises(ValueError):
                module.brand_package(package, repo)

    def test_rejects_unexpected_entrypoint_and_variant(self):
        for entrypoint, variant in (("../outside", "codex"), ("bin/codex", "codex"), ("bin/er", "codex-app-server")):
            with self.subTest(entrypoint=entrypoint, variant=variant), tempfile.TemporaryDirectory() as tmp:
                repo, package, _ = self.fixture(Path(tmp))
                (package / "codex-package.json").write_text(json.dumps({"entrypoint": entrypoint, "variant": variant}))
                with self.assertRaises(ValueError):
                    module.brand_package(package, repo)

    def test_archives_after_rebranding(self):
        events = []
        with patch.object(module, "assemble_package", side_effect=lambda args: events.append("assemble") or Path("package")), patch.object(module, "brand_package", side_effect=lambda path: events.append("brand")), patch.object(module, "archive_package", side_effect=lambda path, args: events.append("archive")):
            self.assertEqual(module.main([]), 0)
        self.assertEqual(events, ["assemble", "brand", "archive"])

    def test_real_staging_with_prebuilt_fixtures(self):
        # Exercise the real canonical builder, with inert fixture bytes. No
        # compiler, network download, model, or executable invocation is used.
        for target in (
            "x86_64-unknown-linux-gnu",
            "x86_64-pc-windows-msvc",
            "aarch64-apple-darwin",
            "x86_64-apple-darwin",
        ):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                windows = "windows" in target
                root = Path(tmp)
                fixture = root / "fixture"
                fixture.write_bytes(b"inert packaging fixture")
                fixture.chmod(0o755)
                output, archive = root / "out", root / "ercode.zip"
                args = [
                    "--target", target,
                    "--entrypoint-bin", str(fixture),
                    "--code-mode-host-bin", str(fixture),
                    "--exec-server-bin", str(fixture),
                    "--rg-bin", str(fixture),
                    "--strip", "none",
                    "--package-dir", str(output),
                    "--archive-output", str(archive),
                ]
                resources = (
                    ["--codex-command-runner-bin", "--codex-windows-sandbox-setup-bin"]
                    if windows else (["--bwrap-bin"] if "linux" in target else [])
                )
                for flag in resources:
                    args.extend([flag, str(fixture)])
                self.assertEqual(module.main(args), 0)
                name = "er.exe" if windows else "er"
                with zipfile.ZipFile(archive) as packaged:
                    entries = packaged.namelist()
                    canonical = f"bin/{name}"
                    self.assertIn(canonical, entries)
                    self.assertEqual(packaged.read(canonical), fixture.read_bytes())
                    self.assertNotIn("bin/codex.exe" if windows else "bin/codex", entries)
                    self.assertNotIn("bin/ercode.exe" if windows else "bin/ercode", entries)
                    self.assertIn("bin/codex-code-mode-host" + (".exe" if windows else ""), entries)
                    self.assertIn("bin/codex-exec-server" + (".exe" if windows else ""), entries)
                    self.assertIn("codex-path/rg" + (".exe" if windows else ""), entries)
                    if windows:
                        self.assertIn("codex-resources/codex-command-runner.exe", entries)
                        self.assertIn("codex-resources/codex-windows-sandbox-setup.exe", entries)
                    elif "linux" in target:
                        self.assertIn("codex-resources/bwrap", entries)
                    self.assertIn("LICENSE", entries)
                    self.assertIn("NOTICE", entries)
                    self.assertEqual(packaged.read("NOTICE"), (module.REPO_ROOT / "NOTICE").read_bytes())
                    metadata = json.loads(packaged.read("codex-package.json"))
                    self.assertEqual(metadata["entrypoint"], canonical)
                # A second build must not silently replace an existing package.
                with self.assertRaises(RuntimeError):
                    module.main(args)

    def test_rejects_non_cli_before_build(self):
        with patch.object(module, "assemble_package") as build:
            with self.assertRaises(SystemExit):
                module.main(["--variant", "codex-app-server"])
            build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
