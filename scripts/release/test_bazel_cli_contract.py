"""Check fork CLI labels without downloading Bazel's native toolchains."""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]


class BazelCliContractTests(unittest.TestCase):
    def test_upstream_label_aliases_the_only_shipped_binary(self):
        build = (ROOT / "codex-rs/cli/BUILD.bazel").read_text()
        self.assertRegex(
            build,
            r'alias\(\s*name = "codex",\s*actual = ":er",\s*'
            r'visibility = \["//visibility:public"\],\s*\)',
        )
        self.assertIn('binaries_with_build_commit = ["er"]', build)
        self.assertRegex(build, r'release_binary\(\s*binary = ":er",\s*\)')

    def test_daemon_package_test_resolves_its_declared_data(self):
        build = (ROOT / "codex-rs/cli/BUILD.bazel").read_text()
        rule = re.search(r'rust_test\((.*?)\n\)', build, re.S).group(1)
        data = re.search(r'data = \[(.*?)\]', rule).group(1)
        for label in re.findall(r'\$\(rlocationpath ([^)]+)\)', rule):
            self.assertIn(f'"{label}"', data)
        self.assertIn('"CARGO_BIN_EXE_er": "$(rlocationpath :er)"', rule)

    def test_cli_exec_server_uses_the_cargo_binary_name(self):
        test = (ROOT / "codex-rs/cli/tests/exec_server.rs").read_text()
        self.assertIn('cargo_bin("er")', test)
        self.assertNotIn('cargo_bin("codex")', test)


if __name__ == "__main__":
    unittest.main()
