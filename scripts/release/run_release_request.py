#!/usr/bin/env python3
"""Run a narrowly scoped, versioned release request from the publishing branch."""
import argparse
import json
import os
from pathlib import Path
import re
import tarfile
import tempfile
import zipfile

import publish_er

REPO = 'erquren/ercode'
TAG = '0.166.0'
BRANCH = 'refs/heads/release-publish/' + TAG


def validate_request(request, repo, ref):
    if repo != REPO or ref != BRANCH:
        raise ValueError('Release requests are restricted to the ercode publishing branch')
    required = {'tag', 'source_commit', 'mode', 'runs', 'replace_draft_assets'}
    if not required.issubset(request) or set(request) - required - {'approved_replacement_assets'}:
        raise ValueError('Unexpected release request fields')
    if request['tag'] != TAG or not re.fullmatch('[0-9a-f]{40}', request['source_commit']):
        raise ValueError('Invalid release tag or source commit')
    if request['mode'] not in ('inspect', 'check_only', 'publish'):
        raise ValueError('Unknown release request mode')
    if type(request['replace_draft_assets']) is not bool:
        raise ValueError('Replacement must be explicitly true or false')
    if request['mode'] != 'publish' and request['replace_draft_assets']:
        raise ValueError('Read-only requests cannot replace assets')
    if not isinstance(request['runs'], list) or any(type(run) is not int or run <= 0 for run in request['runs']):
        raise ValueError('Build runs must be positive integer IDs')
    if request['mode'] != 'inspect' and not request['runs']:
        raise ValueError('Build runs are required for validation or publication')
    approved = request.get('approved_replacement_assets', {})
    if not isinstance(approved, dict) or any(not re.fullmatch('[1-9][0-9]*', key) or not isinstance(name, str) for key, name in approved.items()):
        raise ValueError('Approved replacements must map exact positive asset IDs to names')
    if request['replace_draft_assets'] and not approved:
        raise ValueError('Replacement requires exact approved asset IDs and names')


def inspect_release(repo, tag, root):
    """Read metadata and digests only. Never extract or execute downloaded files."""
    root = root / "existing-draft"
    root.mkdir(parents=True, exist_ok=True)
    release = publish_er.find_release(repo, tag)
    if release is None:
        print('No existing release for', tag, flush=True)
        return
    print(json.dumps({'releaseId': release['id'], 'tag': tag, 'draft': release['draft']}, ensure_ascii=False), flush=True)
    for asset in publish_er.release_assets(repo, release['id']):
        report = {key: asset.get(key) for key in ('id', 'name', 'size', 'digest')}
        filename = asset['name']
        allowed = {f'er-{tag}-{platform}' + ('.zip' if platform.startswith('windows') else '.tar.gz') for platform in publish_er.TARGETS}
        if filename in allowed:
            path = root / filename
            publish_er.download(f"/repos/{repo}/releases/assets/{asset['id']}", path, 'application/octet-stream')
            publish_er.verify_uploaded(repo, asset, path, root)
            report['downloadSha256'] = publish_er.digest(path)
            if filename.endswith('.zip'):
                with zipfile.ZipFile(path) as bundle:
                    names = bundle.namelist()
                    for name in names:
                        if name.endswith(('/BUILD-INFO.json', '/codex-package.json')) or name in ('BUILD-INFO.json', 'codex-package.json'):
                            info = bundle.getinfo(name)
                            if info.file_size > 1024 * 1024:
                                raise ValueError('Oversized package metadata')
                            report[name] = json.loads(bundle.read(name))
            else:
                with tarfile.open(path) as bundle:
                    names = bundle.getnames()
                    for member in bundle.getmembers():
                        if member.name.endswith(('/BUILD-INFO.json', '/codex-package.json')) or member.name in ('BUILD-INFO.json', 'codex-package.json'):
                            if not member.isfile() or member.size > 1024 * 1024:
                                raise ValueError('Invalid package metadata')
                            report[member.name] = json.load(bundle.extractfile(member))
            report['fileCount'] = len(names)
            report['runtimeFiles'] = sorted(name for name in names if any(part in name for part in ('/bin/', 'codex-resources/', 'codex-path/')))
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


def execute(request, repo, ref):
    validate_request(request, repo, ref)
    commit = publish_er.tag_commit(repo, request['tag'])
    if commit != request['source_commit']:
        raise ValueError('Release tag no longer matches the reviewed source commit')
    with tempfile.TemporaryDirectory(prefix='er-release-request-') as directory:
        root = Path(directory)
        if request['mode'] == 'inspect':
            inspect_release(repo, request['tag'], root)
            print('Read-only draft inspection complete; no release changes made', flush=True)
            return
        builds = publish_er.wait_for_builds(repo, request['runs'], request['tag'])
        files = publish_er.collect(repo, builds, request['tag'], commit, root)
        if publish_er.tag_commit(repo, request['tag']) != commit:
            raise ValueError('Release tag moved during verification')
        if request['mode'] == 'check_only':
            inspect_release(repo, request['tag'], root)
            print('All four packages verified; no release changes made', flush=True)
            return
        publish_er.publish(repo, request['tag'], commit, files, root, request['replace_draft_assets'],
                           approved_replacement_assets={int(key): name for key, name in request.get('approved_replacement_assets', {}).items()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    args = parser.parse_args()
    execute(json.loads(args.request.read_text()), os.environ['GITHUB_REPOSITORY'], os.environ['GITHUB_REF'])


if __name__ == '__main__':
    main()
