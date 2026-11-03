"""Offline checks for local, pinned voice-release orchestration."""
import io
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import build_ercode_voice as voice


class VoiceReleaseTests(unittest.TestCase):
    def test_linux_maps_to_gnu_and_local_only_bazel(self):
        target, prefix = voice.TARGETS["x86_64-unknown-linux-musl"]
        self.assertEqual(target, "x86_64-unknown-linux-gnu")
        self.assertEqual(prefix, "linux_x86_64")
        command = voice.bazel_command("x86_64-unknown-linux-musl", Path("/tmp/bazel"), {})
        for flag in ("--jobs=2", "--remote_cache=", "--remote_executor=", "--bes_backend=", "--spawn_strategy=local", "--nohome_rc", "--nosystem_rc"):
            self.assertIn(flag, command)
        self.assertFalse(any("buildbuddy" in argument for argument in command))

    def test_windows_requires_verified_tools(self):
        with self.assertRaises(ValueError):
            voice.bazel_command("x86_64-pc-windows-msvc", Path("/tmp/bazel"), {})
        env = {
            "VOICE_WINDOWS_BAZEL_REPOSITORY": "C:/tools",
            "CODEX_BAZEL_WINDOWS_PATH": "C:/sdk;C:/Windows",
            "SYSTEMROOT": "C:/Windows",
            "PROCESSOR_ARCHITECTURE": "AMD64",
        }
        command = voice.bazel_command("x86_64-pc-windows-msvc", Path("/tmp/bazel"), env)
        self.assertIn("--host_platform=//:local_windows_msvc", command)
        self.assertIn("--inject_repository=voice_windows_tools=C:/tools", command)
        self.assertIn("--action_env=PATH=C:/sdk;C:/Windows", command)
        self.assertIn("--action_env=SystemRoot=C:/Windows", command)
        self.assertIn("--workspace_status_command=./scripts/workspace-status.cmd", command)

    def test_macos_minimum_and_target_mapping(self):
        for target in ("aarch64-apple-darwin", "x86_64-apple-darwin"):
            self.assertEqual(voice.TARGETS[target][0], target)
            self.assertIn("--macos_minimum_os=14.0", voice.bazel_command(target, Path("/tmp/bazel"), {}))

    def test_download_verifies_hash_and_size_before_accepting(self):
        data = b"pinned test input"
        pin = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "input.tar.gz"
            with patch.object(voice.urllib.request, "urlopen", return_value=io.BytesIO(data)):
                voice.download_pinned("https://example.invalid/input", output, pin)
            self.assertEqual(output.read_bytes(), data)
            for bad_data in (data + b"extra", data[:-1], b"x" * len(data)):
                with patch.object(voice.urllib.request, "urlopen", return_value=io.BytesIO(bad_data)):
                    with self.assertRaises(ValueError):
                        voice.download_pinned("https://example.invalid/input", output, pin)
                self.assertEqual(output.read_bytes(), data)
                self.assertFalse(output.with_suffix(".gz.partial").exists())


if __name__ == "__main__":
    unittest.main()
