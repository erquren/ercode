import unittest
import urllib.error
from unittest.mock import patch
import run_release_request as request


class RequestTests(unittest.TestCase):
    def fixture(self, mode="inspect"):
        return {
            "tag": "0.170.0",
            "source_commit": "a" * 40,
            "mode": mode,
            "runs": [] if mode == "inspect" else [123],
            "replace_draft_assets": False,
        }

    def test_read_only_inspection_never_collects_or_publishes(self):
        value = self.fixture()
        with (
            patch.object(request.publish_er, "tag_commit", return_value="a" * 40),
            patch.object(request, "inspect_release") as inspect,
            patch.object(request.publish_er, "publish") as publish,
            patch.object(request.publish_er, "collect") as collect,
        ):
            request.execute(value, request.REPO, request.BRANCH)
        inspect.assert_called_once()
        collect.assert_not_called()
        publish.assert_not_called()

    def test_check_only_never_publishes(self):
        value = self.fixture("check_only")
        with (
            patch.object(request.publish_er, "tag_commit", return_value="a" * 40),
            patch.object(request, "inspect_release"),
            patch.object(request.publish_er, "wait_for_builds", return_value={}),
            patch.object(request.publish_er, "collect", return_value=[]),
            patch.object(request.publish_er, "publish") as publish,
        ):
            request.execute(value, request.REPO, request.BRANCH)
        publish.assert_not_called()

    def test_refuses_wrong_branch_repository_version_and_unknown_mode(self):
        for repo, ref in [
            ("someone/ercode", request.BRANCH),
            (request.REPO, "refs/heads/main"),
        ]:
            with self.assertRaises(ValueError):
                request.validate_request(self.fixture(), repo, ref)
        for key, value in [
            ("tag", "0.170.1"),
            ("mode", "anything"),
            ("runs", ["123"]),
            ("source_commit", "invalid"),
            ("replace_draft_assets", True),
        ]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                request.validate_request(
                    {**self.fixture(), key: value}, request.REPO, request.BRANCH
                )

    def test_publish_replacement_needs_exact_ids_and_passes_them_to_publisher(self):
        value = {**self.fixture("publish"), "replace_draft_assets": True}
        with self.assertRaisesRegex(ValueError, "exact approved"):
            request.validate_request(value, request.REPO, request.BRANCH)
        value["approved_replacement_assets"] = {
            "704827103": "er-0.170.0-linux-x86_64-musl.tar.gz"
        }
        with (
            patch.object(request.publish_er, "tag_commit", return_value="a" * 40),
            patch.object(request.publish_er, "wait_for_builds", return_value={}),
            patch.object(request.publish_er, "collect", return_value=[]),
            patch.object(request.publish_er, "publish") as publish,
        ):
            request.execute(value, request.REPO, request.BRANCH)
        self.assertEqual(
            publish.call_args.kwargs["approved_replacement_assets"],
            {704827103: value["approved_replacement_assets"]["704827103"]},
        )

    def test_moved_tag_fails_before_any_action(self):
        with (
            patch.object(request.publish_er, "tag_commit", return_value="b" * 40),
            patch.object(request, "inspect_release") as inspect,
        ):
            with self.assertRaisesRegex(ValueError, "source commit"):
                request.execute(self.fixture(), request.REPO, request.BRANCH)
        inspect.assert_not_called()

    def test_missing_tag_created_only_after_all_packages_verified(self):
        events = []
        with (
            patch.object(request, "optional_tag_commit", return_value=None),
            patch.object(request.publish_er, "tag_commit", return_value="a" * 40),
            patch.object(request.publish_er, "wait_for_builds", return_value={}),
            patch.object(
                request.publish_er,
                "collect",
                side_effect=lambda *args: events.append("verified") or [],
            ),
            patch.object(
                request.publish_er,
                "api",
                side_effect=lambda *args, **kwargs: events.append("created"),
            ) as api,
            patch.object(
                request.publish_er,
                "publish",
                side_effect=lambda *args, **kwargs: events.append("published"),
            ),
        ):
            request.execute(self.fixture("publish"), request.REPO, request.BRANCH)
        self.assertEqual(events, ["verified", "created", "published"])
        api.assert_called_once_with(
            f"/repos/{request.REPO}/git/refs",
            method="POST",
            data={"ref": f"refs/tags/{request.TAG}", "sha": "a" * 40},
        )

    def test_missing_tag_check_only_does_not_write(self):
        with (
            patch.object(request, "optional_tag_commit", return_value=None),
            patch.object(request, "inspect_release"),
            patch.object(request.publish_er, "wait_for_builds", return_value={}),
            patch.object(request.publish_er, "collect", return_value=[]),
            patch.object(request.publish_er, "api") as api,
            patch.object(request.publish_er, "publish") as publish,
        ):
            request.execute(self.fixture("check_only"), request.REPO, request.BRANCH)
        api.assert_not_called()
        publish.assert_not_called()

    def test_failed_package_verification_never_creates_tag(self):
        with (
            patch.object(request, "optional_tag_commit", return_value=None),
            patch.object(request.publish_er, "wait_for_builds", return_value={}),
            patch.object(
                request.publish_er, "collect", side_effect=ValueError("bad source")
            ),
            patch.object(request.publish_er, "api") as api,
            patch.object(request.publish_er, "publish") as publish,
        ):
            with self.assertRaisesRegex(ValueError, "bad source"):
                request.execute(self.fixture("publish"), request.REPO, request.BRANCH)
        api.assert_not_called()
        publish.assert_not_called()

    def test_concurrent_conflicting_tag_never_overwritten(self):
        with (
            patch.object(
                request, "optional_tag_commit", side_effect=[None, None, "b" * 40]
            ),
            patch.object(request.publish_er, "wait_for_builds", return_value={}),
            patch.object(request.publish_er, "collect", return_value=[]),
            patch.object(request.publish_er, "api") as api,
            patch.object(request.publish_er, "publish") as publish,
        ):
            with self.assertRaisesRegex(ValueError, "moved"):
                request.execute(self.fixture("publish"), request.REPO, request.BRANCH)
        api.assert_not_called()
        publish.assert_not_called()

    def test_create_conflict_is_not_retried_or_published(self):
        conflict = urllib.error.HTTPError("url", 422, "already exists", {}, None)
        with (
            patch.object(request, "optional_tag_commit", return_value=None),
            patch.object(request.publish_er, "wait_for_builds", return_value={}),
            patch.object(request.publish_er, "collect", return_value=[]),
            patch.object(request.publish_er, "api", side_effect=conflict) as api,
            patch.object(request.publish_er, "publish") as publish,
        ):
            with self.assertRaises(urllib.error.HTTPError):
                request.execute(self.fixture("publish"), request.REPO, request.BRANCH)
        api.assert_called_once()
        publish.assert_not_called()

    def test_existing_matching_tag_never_written(self):
        with (
            patch.object(request.publish_er, "tag_commit", return_value="a" * 40),
            patch.object(request.publish_er, "api") as api,
        ):
            request.ensure_verified_tag(request.REPO, request.TAG, "a" * 40)
        api.assert_not_called()

    def test_only_404_is_treated_as_missing(self):
        for code in [404, 403, 500]:
            with (
                self.subTest(code=code),
                patch.object(
                    request.publish_er,
                    "tag_commit",
                    side_effect=urllib.error.HTTPError("url", code, "error", {}, None),
                ),
            ):
                if code == 404:
                    self.assertIsNone(
                        request.optional_tag_commit(request.REPO, request.TAG)
                    )
                else:
                    with self.assertRaises(urllib.error.HTTPError):
                        request.optional_tag_commit(request.REPO, request.TAG)


if __name__ == "__main__":
    unittest.main()
