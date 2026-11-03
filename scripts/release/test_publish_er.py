#!/usr/bin/env python3
"""Offline regression tests: python3 -m unittest discover -s scripts/release."""
import hashlib
import http.client
import io
import json
import os
import tarfile
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error
import zipfile

import publish_er as publish


class Response(io.BytesIO):
    def __init__(self, value=b'{}', headers=None):
        super().__init__(value)
        self.headers = headers or {}


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'package.zip'
        self.path.write_bytes(b'package')
        self.asset = {'id': 9, 'name': self.path.name, 'size': 7,
                      'digest': 'sha256:' + publish.digest(self.path)}
        self.release = {'id': 4, 'draft': True, 'tag_name': '1.2.3',
                        'upload_url': 'https://uploads.github.com/repos/o/r/releases/4/assets{?name,label}'}
        self.env = patch.dict(os.environ, GH_TOKEN='secret-token')
        self.env.start()
        self.addCleanup(self.env.stop)
        self.sleep = patch.object(publish.time, 'sleep').start()
        self.addCleanup(patch.stopall)

    def test_get_retries_disconnect(self):
        opener = Mock()
        opener.open.side_effect = [http.client.RemoteDisconnected(), Response(b'{"ok": true}')]
        with patch.object(publish.urllib.request, 'build_opener', return_value=opener):
            self.assertEqual(publish.api('/test'), {'ok': True})
        self.assertEqual(opener.open.call_count, 2)

    def test_retry_is_bounded(self):
        operation = Mock(side_effect=http.client.RemoteDisconnected())
        with self.assertRaises(http.client.RemoteDisconnected):
            publish.retry_read(operation)
        self.assertEqual(operation.call_count, publish.MAX_ATTEMPTS)

    def test_auth_failure_not_retried(self):
        error = urllib.error.HTTPError('url', 401, 'unauthorized', {}, None)
        operation = Mock(side_effect=error)
        with self.assertRaises(urllib.error.HTTPError):
            publish.retry_read(operation)
        operation.assert_called_once()

    def test_rate_limit_retry_after(self):
        error = urllib.error.HTTPError('url', 429, 'rate limited', {'Retry-After': '999'}, None)
        operation = Mock(side_effect=[error, 'ok'])
        self.assertEqual(publish.retry_read(operation), 'ok')
        self.sleep.assert_called_once_with(60)

    def test_mutating_api_never_blindly_retries(self):
        for method in ('POST', 'PATCH', 'DELETE'):
            opener = Mock()
            opener.open.side_effect = http.client.RemoteDisconnected()
            with patch.object(publish.urllib.request, 'build_opener', return_value=opener):
                with self.assertRaises(http.client.RemoteDisconnected):
                    publish.api('/test', method)
            opener.open.assert_called_once()

    def test_api_redirect_cannot_forward_credentials(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError('url', 302, 'redirect', {'Location': 'https://evil.test'}, None)
        with patch.object(publish.urllib.request, 'build_opener', return_value=opener) as builder:
            with self.assertRaises(urllib.error.HTTPError):
                publish.api('/test')
            builder.assert_called_with(publish.NoRedirect)
        self.assertIsNone(publish.NoRedirect().redirect_request(None, None, None, None, None, None))
        with self.assertRaises(ValueError):
            publish.api('https://evil.test/')

    def test_redirected_download_has_no_token(self):
        github, storage = Mock(), Mock()
        github.open.side_effect = urllib.error.HTTPError('url', 302, 'redirect', {'Location': 'https://storage.test/signed'}, None)
        storage.open.return_value = Response(b'complete', {'Content-Length': '8'})
        with patch.object(publish.urllib.request, 'build_opener', side_effect=[github, storage]):
            publish.download('/artifact', self.path)
        self.assertEqual(github.open.call_args.args[0].get_header('Authorization'), 'Bearer secret-token')
        storage.open.assert_called_once_with('https://storage.test/signed', timeout=120)
        self.assertEqual(self.path.read_bytes(), b'complete')

    def test_partial_download_is_atomic_and_retried(self):
        opener = Mock()
        opener.open.side_effect = [Response(b'part', {'Content-Length': '100'}), Response(b'complete')]
        with patch.object(publish.urllib.request, 'build_opener', return_value=opener):
            publish.download('/artifact', self.path)
        self.assertEqual(self.path.read_bytes(), b'complete')
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_failed_download_preserves_old_file(self):
        opener = Mock()
        opener.open.side_effect = lambda *a, **kw: Response(b'part', {'Content-Length': '100'})
        with patch.object(publish.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaises(http.client.IncompleteRead):
                publish.download('/artifact', self.path)
        self.assertEqual(self.path.read_bytes(), b'package')
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_insecure_storage_redirect_refused(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError('url', 302, 'redirect', {'Location': 'http://storage.test'}, None)
        with patch.object(publish.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaises(ValueError):
                publish.download('/artifact', self.path)

    def test_pagination(self):
        with patch.object(publish, 'api', side_effect=[{'jobs': list(range(100))}, {'jobs': [100]}]) as api:
            self.assertEqual(list(publish.paginated('/jobs', 'jobs')), list(range(101)))
        self.assertIn('page=2', api.call_args.args[0])

    def test_nested_annotated_tag(self):
        objects = [{'object': {'type': 'tag', 'sha': 'a'}}, {'object': {'type': 'tag', 'sha': 'b'}},
                   {'object': {'type': 'commit', 'sha': 'source'}}]
        with patch.object(publish, 'api', side_effect=objects):
            self.assertEqual(publish.tag_commit('o/r', '1.2.3'), 'source')

    def test_digest_failure(self):
        with self.assertRaises(AssertionError):
            publish.verify_uploaded('o/r', dict(self.asset, digest='sha256:wrong'), self.path, self.root)

    def test_missing_digest_download_verification(self):
        def download(path, dest, accept):
            dest.write_bytes(b'package')
        with patch.object(publish, 'download', side_effect=download) as fetch:
            publish.verify_uploaded('o/r', dict(self.asset, digest=None), self.path, self.root)
        fetch.assert_called_once()
        self.assertFalse((self.root / 'verify-9').exists())

    def test_published_release_refused_before_writes(self):
        with patch.object(publish, 'tag_commit', return_value='source'), \
                patch.object(publish, 'find_release', return_value=dict(self.release, draft=False)), \
                patch.object(publish, 'api') as api:
            with self.assertRaisesRegex(RuntimeError, 'already published'):
                publish.publish('o/r', '1.2.3', 'source', [self.path], self.root)
        api.assert_not_called()

    def test_uncertain_create_reconciles_success(self):
        with patch.object(publish, 'find_release', side_effect=[None, self.release]), \
                patch.object(publish, 'tag_commit', return_value='source'), \
                patch.object(publish, 'api', side_effect=http.client.RemoteDisconnected()) as api:
            self.assertEqual(publish.create_draft('o/r', '1.2.3', 'source'), self.release)
        api.assert_called_once()

    def test_uncertain_create_absent_stops(self):
        with patch.object(publish, 'find_release', return_value=None), \
                patch.object(publish, 'tag_commit', return_value='source'), \
                patch.object(publish, 'api', side_effect=http.client.RemoteDisconnected()) as api:
            with self.assertRaisesRegex(RuntimeError, 'outcome uncertain'):
                publish.create_draft('o/r', '1.2.3', 'source')
        api.assert_called_once()

    def test_existing_asset_preserved(self):
        for asset in (self.asset, dict(self.asset, digest='sha256:wrong')):
            with patch.object(publish, 'fresh_draft', return_value=self.release), \
                    patch.object(publish, 'release_assets', return_value=[asset]), patch.object(publish, 'api') as api:
                if asset == self.asset:
                    publish.upload_asset('o/r', self.release, self.path, self.root)
                else:
                    with self.assertRaises(AssertionError):
                        publish.upload_asset('o/r', self.release, self.path, self.root)
                api.assert_not_called()

    def test_uncertain_upload_reconciles_success(self):
        with patch.object(publish, 'fresh_draft', return_value=self.release), \
                patch.object(publish, 'release_assets', side_effect=[[], [self.asset]]), \
                patch.object(publish, 'api', side_effect=http.client.RemoteDisconnected()) as api:
            publish.upload_asset('o/r', self.release, self.path, self.root)
        api.assert_called_once()

    def test_uncertain_upload_absent_stops(self):
        with patch.object(publish, 'fresh_draft', return_value=self.release), \
                patch.object(publish, 'release_assets', return_value=[]), \
                patch.object(publish, 'api', side_effect=http.client.RemoteDisconnected()) as api:
            with self.assertRaisesRegex(RuntimeError, 'outcome uncertain'):
                publish.upload_asset('o/r', self.release, self.path, self.root)
        api.assert_called_once()

    def test_collect_provenance_and_checksum(self):
        platform = 'windows-x86_64'
        target = publish.TARGETS[platform]
        filename = 'er-1.2.3-' + platform + '.zip'
        metadata = {'sourceCommit': 'source', 'target': target, 'version': '1.2.3', 'reusedBinaries': False}

        def download(path, destination):
            inner = io.BytesIO()
            with zipfile.ZipFile(inner, 'w') as bundle:
                bundle.writestr('er-1.2.3-' + platform + '/BUILD-INFO.json', json.dumps(metadata))
            content = inner.getvalue()
            with zipfile.ZipFile(destination, 'w') as outer:
                outer.writestr(filename, content)
                outer.writestr(filename + '.sha256', hashlib.sha256(content).hexdigest() + '  ' + filename)

        with patch.object(publish, 'TARGETS', {platform: target}), \
                patch.object(publish, 'paginated', return_value=[{'id': 1, 'name': 'er-1.2.3-' + platform, 'expired': False}]), \
                patch.object(publish, 'download', side_effect=download):
            self.assertEqual(len(publish.collect('o/r', {platform: 1}, '1.2.3', 'source', self.root)), 2)
            for key, wrong in [('sourceCommit', 'other'), ('target', 'wrong'), ('version', '1.2.4'), ('reusedBinaries', True)]:
                old = metadata[key]
                metadata[key] = wrong
                with self.assertRaises(AssertionError):
                    publish.collect('o/r', {platform: 1}, '1.2.3', 'source', self.root)
                metadata[key] = old

    def test_uncertain_publish_reconciles_without_second_patch(self):
        for completed in (True, False):
            observed = dict(self.release)
            def api(path, method="GET", data=None):
                if method == "PATCH":
                    if completed:
                        observed.update(data)
                    raise http.client.RemoteDisconnected()
                return observed
            with patch.object(publish, 'tag_commit', return_value='source'), \
                    patch.object(publish, 'create_draft', return_value=self.release), \
                    patch.object(publish, 'upload_asset'), \
                    patch.object(publish, 'fresh_draft', return_value=self.release), \
                    patch.object(publish, 'release_assets', return_value=[self.asset]), \
                    patch.object(publish, 'api', side_effect=api) as request:
                observed['html_url'] = 'https://github.com/o/r/releases/tag/1.2.3'
                if completed:
                    publish.publish('o/r', '1.2.3', 'source', [self.path], self.root)
                else:
                    with self.assertRaisesRegex(RuntimeError, 'outcome uncertain'):
                        publish.publish('o/r', '1.2.3', 'source', [self.path], self.root)
                self.assertEqual(sum(call.args[1:2] == ('PATCH',) for call in request.call_args_list), 1)

    def test_tag_move_stops_publication(self):
        with patch.object(publish, 'tag_commit', return_value='changed'), patch.object(publish, 'api') as api:
            with self.assertRaisesRegex(AssertionError, 'tag moved'):
                publish.publish('o/r', '1.2.3', 'source', [self.path], self.root)
        api.assert_not_called()

    def test_corrupt_artifact_checksum_rejected(self):
        platform = 'linux-x86_64-musl'
        filename = 'er-1.2.3-' + platform + '.tar.gz'
        def download(path, destination):
            with zipfile.ZipFile(destination, 'w') as outer:
                outer.writestr(filename, b'corrupt')
                outer.writestr(filename + '.sha256', 'wrong  ' + filename)
        with patch.object(publish, 'TARGETS', {platform: publish.TARGETS[platform]}), \
                patch.object(publish, 'paginated', return_value=[{'id': 1, 'name': 'er-1.2.3-' + platform, 'expired': False}]), \
                patch.object(publish, 'download', side_effect=download):
            with self.assertRaises(AssertionError):
                publish.collect('o/r', {platform: 1}, '1.2.3', 'source', self.root)

    def test_replace_requires_all_four_verified_packages(self):
        with self.assertRaisesRegex(RuntimeError, 'all four'):
            publish.verify_replacement_files([self.path], '1.2.3', 'source')
        files = []
        for platform, target in publish.TARGETS.items():
            basename = 'er-1.2.3-' + platform
            archive = self.root / (basename + ('.zip' if platform.startswith('windows') else '.tar.gz'))
            metadata = json.dumps({'sourceCommit': 'source', 'target': target,
                                   'version': '1.2.3', 'reusedBinaries': False}).encode()
            if platform.startswith('windows'):
                with zipfile.ZipFile(archive, 'w') as bundle:
                    bundle.writestr(basename + '/BUILD-INFO.json', metadata)
            else:
                with tarfile.open(archive, 'w:gz') as bundle:
                    info = tarfile.TarInfo(basename + '/BUILD-INFO.json')
                    info.size = len(metadata)
                    bundle.addfile(info, io.BytesIO(metadata))
            checksum = archive.with_name(archive.name + '.sha256')
            checksum.write_text(publish.digest(archive) + '  ' + archive.name)
            files.extend([archive, checksum])
        publish.verify_replacement_files(files, '1.2.3', 'source')
        files[1].write_text('wrong  ' + files[0].name)
        with self.assertRaises(AssertionError):
            publish.verify_replacement_files(files, '1.2.3', 'source')

    def test_replace_incomplete_files_prevents_external_writes(self):
        with patch.object(publish, 'tag_commit', return_value='source'), patch.object(publish, 'create_draft') as create:
            with self.assertRaisesRegex(RuntimeError, 'all four'):
                publish.publish('o/r', '1.2.3', 'source', [self.path], self.root, True)
        create.assert_not_called()

    def test_opt_in_replaces_exact_mismatched_draft_asset(self):
        old = dict(self.asset, id=8, digest='sha256:old')
        with patch.object(publish, 'fresh_draft', return_value=self.release), \
                patch.object(publish, 'release_assets', side_effect=[[old], [old]]), \
                patch.object(publish, 'api', side_effect=[None, self.asset]) as api:
            publish.upload_asset('o/r', self.release, self.path, self.root, True)
        self.assertEqual(api.call_args_list[0].args, ('/repos/o/r/releases/assets/8', 'DELETE'))
        self.assertEqual(api.call_args_list[1].args[1], 'POST')

    def test_uncertain_delete_reconciles_absence_or_stops(self):
        for remaining in ([], [self.asset]):
            with patch.object(publish, 'fresh_draft', return_value=self.release), \
                    patch.object(publish, 'release_assets', side_effect=[[self.asset], remaining]), \
                    patch.object(publish, 'api', side_effect=http.client.RemoteDisconnected()) as api:
                if remaining:
                    with self.assertRaisesRegex(RuntimeError, 'deletion outcome uncertain'):
                        publish.delete_draft_asset('o/r', self.release, self.asset)
                else:
                    publish.delete_draft_asset('o/r', self.release, self.asset)
            api.assert_called_once_with('/repos/o/r/releases/assets/9', 'DELETE')

    def test_delete_refuses_published_release(self):
        with patch.object(publish, 'api', return_value=dict(self.release, draft=False)) as api:
            with self.assertRaisesRegex(RuntimeError, 'already published'):
                publish.delete_draft_asset('o/r', self.release, self.asset)
        api.assert_called_once_with('/repos/o/r/releases/4')

    def test_check_only_never_publishes(self):
        with patch('sys.argv', ['publish_er.py', '--repo', 'o/r', '--tag', '1.2.3', '--runs', '1', '--check-only']), \
                patch.object(publish, 'tag_commit', return_value='source'), \
                patch.object(publish, 'wait_for_builds', return_value={}), \
                patch.object(publish, 'collect', return_value=[]), patch.object(publish, 'publish') as write:
            publish.main()
        write.assert_not_called()


if __name__ == '__main__':
    unittest.main()
