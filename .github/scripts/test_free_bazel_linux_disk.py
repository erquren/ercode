import unittest
from unittest.mock import call, patch

import free_bazel_linux_disk


class FreeBazelLinuxDiskTest(unittest.TestCase):
    def test_only_fixed_unused_sdks_are_removed(self):
        env = {
            "RUNNER_ENVIRONMENT": "github-hosted",
            "RUNNER_OS": "Linux",
            "ImageOS": "ubuntu24",
        }
        with (
            patch.dict("os.environ", env, clear=True),
            patch("os.path.isdir", return_value=True),
            patch("os.path.islink", return_value=False),
            patch("subprocess.run") as run,
        ):
            free_bazel_linux_disk.main()
        self.assertEqual(
            run.call_args_list,
            [
                call(["df", "-h", "/"], check=True),
                call(["du", "-sh", "/usr/local/lib/android"], check=True),
                call(["sudo", "rm", "-rf", "--", "/usr/local/lib/android"], check=True),
                call(["du", "-sh", "/usr/share/dotnet"], check=True),
                call(["sudo", "rm", "-rf", "--", "/usr/share/dotnet"], check=True),
                call(["df", "-h", "/"], check=True),
            ],
        )

    def test_other_environments_are_untouched(self):
        for key, value in (
            ("RUNNER_ENVIRONMENT", "self-hosted"),
            ("RUNNER_OS", "Windows"),
            ("ImageOS", "ubuntu22"),
        ):
            env = {
                "RUNNER_ENVIRONMENT": "github-hosted",
                "RUNNER_OS": "Linux",
                "ImageOS": "ubuntu24",
                key: value,
            }
            with (
                patch.dict("os.environ", env, clear=True),
                patch("subprocess.run") as run,
            ):
                free_bazel_linux_disk.main()
                run.assert_not_called()

    def test_missing_sdk_and_symlink_are_untouched(self):
        env = {
            "RUNNER_ENVIRONMENT": "github-hosted",
            "RUNNER_OS": "Linux",
            "ImageOS": "ubuntu24",
        }
        for exists, symlink in ((False, False), (True, True)):
            with (
                patch.dict("os.environ", env, clear=True),
                patch("os.path.isdir", return_value=exists),
                patch("os.path.islink", return_value=symlink),
                patch("subprocess.run") as run,
            ):
                free_bazel_linux_disk.main()
                self.assertEqual(
                    run.call_args_list, [call(["df", "-h", "/"], check=True)] * 2
                )


if __name__ == "__main__":
    unittest.main()
