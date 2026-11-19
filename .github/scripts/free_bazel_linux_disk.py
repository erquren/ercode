"""Reclaim unrelated SDK space on ephemeral GitHub-hosted Ubuntu 24 runners."""

import os
import subprocess

UNUSED_SDK_PATHS = ("/usr/local/lib/android", "/usr/share/dotnet")


def main() -> None:
    # Never clean developer machines, self-hosted runners, or other images.
    if (
        os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
        or os.environ.get("RUNNER_OS") != "Linux"
        or os.environ.get("ImageOS") != "ubuntu24"
    ):
        print("Skipping SDK cleanup outside GitHub-hosted Ubuntu 24.")
        return

    subprocess.run(["df", "-h", "/"], check=True)
    for path in UNUSED_SDK_PATHS:
        if not os.path.isdir(path) or os.path.islink(path):
            continue
        subprocess.run(["du", "-sh", path], check=True)
        # Fixed SDK allowlist only. Preserve build tools, repository caches,
        # Docker, the workspace, and all sandbox/security configuration.
        subprocess.run(["sudo", "rm", "-rf", "--", path], check=True)
    subprocess.run(["df", "-h", "/"], check=True)


if __name__ == "__main__":
    main()
