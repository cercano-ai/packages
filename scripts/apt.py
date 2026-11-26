#!/usr/bin/env python3
"""Fail-closed apt promotion. Standard library only; never executes downloaded code."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile

SOURCE = 'cercano-ai/Cercano'
WORKFLOW = '.github/workflows/release-macos.yml'
HELPER_REVISION = 'cf406792ecb487c9492a6bd259aa36b807b7f743'
PREFIX = 'apt-snapshot-'
LIMIT = 900 * 1024 * 1024  # leave room beneath Pages' 1 GB site limit


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_name(name):
    p = PurePosixPath(name)
    require(name and not p.is_absolute() and all(x not in ('', '.', '..') for x in name.split('/'))
            and re.fullmatch(r'[A-Za-z0-9_+./~-]+', name), 'unsafe archive name')
    return p


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class API:
    def __init__(self, repo, token):
        self.root = 'https://api.github.com/repos/' + repo
        self.token = token

    def request(self, path, method='GET', data=None, binary=False, accept=None):
        url = self.root + path
        headers = {'Authorization': 'Bearer ' + self.token,
                   'Accept': accept or ('application/octet-stream' if binary else 'application/vnd.github+json'),
                   'X-GitHub-Api-Version': '2022-11-28'}
        if data is not None:
            data = json.dumps(data).encode()
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            response = urllib.request.build_opener(NoRedirect).open(req, timeout=60)
        except urllib.error.HTTPError as e:
            if binary and e.code == 302:
                target = e.headers['Location']
                require(target.startswith('https://'), 'non-HTTPS asset redirect')
                # Never forward an API credential to blob storage.
                response = urllib.request.urlopen(target, timeout=120)
            else:
                raise RuntimeError('GitHub API request failed: HTTP ' + str(e.code)) from None
        with response:
            result = response.read(LIMIT + 1)
        require(len(result) <= LIMIT, 'download too large')
        return result if binary else json.loads(result)

    def pages(self, path, field=None):
        result = []
        for page in range(1, 101):
            rows = self.request(path + ('&' if '?' in path else '?') + f'per_page=100&page={page}')
            rows = rows[field] if field else rows
            result.extend(rows)
            if len(rows) < 100:
                return result
        raise ValueError('pagination bound exceeded')

    def upload(self, release_id, name, data):
        url = f'https://uploads.github.com/repos/{os.environ["GITHUB_REPOSITORY"]}/releases/{release_id}/assets?name={name}'
        req = urllib.request.Request(url, data=data, method='POST', headers={
            'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/octet-stream',
            'Accept': 'application/vnd.github+json'})
        with urllib.request.build_opener(NoRedirect).open(req, timeout=180) as response:
            return json.load(response)


def asset(api, release, name):
    matches = [x for x in api.pages(f'/releases/{release["id"]}/assets') if x['name'] == name]
    require(len(matches) == 1, 'missing or duplicate durable asset: ' + name)
    return api.request(f'/releases/assets/{matches[0]["id"]}', binary=True)


def validate_receipt(receipt, candidate_sha256, tag):
    # This is the four-field format emitted by finalize(), not a versioned schema.
    require(isinstance(receipt, dict)
            and set(receipt) == {'state', 'candidate_sha256', 'page_url', 'run_id'}
            and all(isinstance(value, str) for value in receipt.values()),
            'invalid publication receipt schema')
    require(receipt['state'] == 'published'
            and re.fullmatch(r'[0-9a-f]{64}', receipt['candidate_sha256'])
            and receipt['candidate_sha256'] == candidate_sha256
            and receipt['page_url'] in ('https://cercano-ai.github.io/packages',
                                        'https://cercano-ai.github.io/packages/')
            and re.fullmatch(r'[1-9][0-9]*', receipt['run_id'])
            and tag == PREFIX + receipt['run_id'], 'invalid publication receipt')


def history(api, expected):
    releases = sorted((r for r in api.pages('/releases') if r['tag_name'].startswith(PREFIX)), key=lambda r: r['id'])
    parent = 'EMPTY'
    last = None
    for release in releases:
        require(not release['draft'], 'draft snapshot requires recovery')
        candidate = json.loads(asset(api, release, 'candidate.json'))
        receipt = json.loads(asset(api, release, 'published.json'))
        require(candidate['tag'] == release['tag_name'] and candidate['base'] == parent,
                'broken snapshot chain')
        validate_receipt(receipt, digest(json.dumps(candidate, sort_keys=True).encode()), release['tag_name'])
        parent, last = release['tag_name'], release
    require(parent == expected, 'stale expected base')
    return last


def unpack_snapshot(data, expected_sha, dest):
    require(digest(data) == expected_sha, 'snapshot digest mismatch')
    total = 0
    seen = set()
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        for member in archive:
            safe_name(member.name)
            require(member.isfile() and member.name not in seen, 'snapshot contains link/directory/duplicate')
            seen.add(member.name)
            total += member.size
            require(total <= LIMIT, 'snapshot expands too large')
            target = dest / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())
    require(seen, 'empty snapshot')


def packages_from_zip(data, version, dest):
    files = {}
    total = 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for member in archive.infolist():
            safe_name(member.filename)
            require('/' not in member.filename and not member.is_dir(), 'unexpected package archive layout')
            mode = member.external_attr >> 16
            require(not stat.S_ISLNK(mode) and (stat.S_IFMT(mode) in (0, stat.S_IFREG)), 'nonregular package member')
            require(member.filename not in files and not member.flag_bits & 1, 'duplicate/encrypted archive member')
            total += member.file_size
            require(total <= LIMIT, 'archive expands too large')
            files[member.filename] = archive.read(member)
    require('SHA256SUMS' in files, 'missing package checksum inventory')
    sums = {}
    for line in files.pop('SHA256SUMS').decode('ascii').splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  \./([A-Za-z0-9_+.~-]+)', line)
        require(match is not None, 'invalid checksum line')
        sha, name = match.groups()
        require(name not in sums, 'duplicate checksum')
        sums[name] = sha
    require(set(files) == set(sums), 'checksum inventory differs from archive')
    for name, data in files.items():
        require(digest(data) == sums[name], 'package checksum mismatch')
    debs = {name: data for name, data in files.items() if name.endswith('.deb')}
    require(len(debs) in (3, 5), 'expected three (amd64) or five (amd64 and arm64) Debian packages')
    dest.mkdir()
    for name, data in debs.items():
        (dest / name).write_bytes(data)
    return validate_debs(dest, version)


def deb_identity(path):
    fields = subprocess.check_output(['dpkg-deb', '-f', str(path), 'Package', 'Version', 'Architecture'], text=True)
    parsed = dict(line.split(': ', 1) for line in fields.strip().splitlines())
    pkg, version, arch = (parsed[x] for x in ('Package', 'Version', 'Architecture'))
    require(pkg in ('cercano', 'cercano-agent', 'cercano-cli') and arch in ('amd64', 'arm64', 'all'), 'unexpected package identity')
    require(re.fullmatch(r'[0-9A-Za-z.+:~\-]+', version), 'unsafe Debian version')
    return pkg, version, arch


# One architecture-independent metapackage, plus both components for amd64 and, optionally, arm64.
AMD64_SET = {('cercano', 'all'), ('cercano-agent', 'amd64'), ('cercano-cli', 'amd64')}
ARM64_COMPONENTS = {('cercano-agent', 'arm64'), ('cercano-cli', 'arm64')}


def validate_debs(dest, version):
    identities = [deb_identity(p) for p in dest.glob('*.deb')]
    present = [(i[0], i[2]) for i in identities]
    require(len(present) == len(set(present)) and set(present) in (AMD64_SET, AMD64_SET | ARM64_COMPONENTS)
            and all(i[1] == version for i in identities), 'package version/set mismatch')
    return identities


def immutable_pool(site, debs):
    existing = {}
    names = {}
    for path in site.glob('pool/**/*.deb'):
        identity = deb_identity(path)
        sha = digest(path.read_bytes())
        require(identity not in existing or existing[identity] == sha, 'prior pool identity conflict')
        require(path.name not in names or names[path.name] == sha, 'prior pool filename conflict')
        existing[identity], names[path.name] = sha, sha
    for path in debs.glob('*.deb'):
        identity, sha = deb_identity(path), digest(path.read_bytes())
        require(identity not in existing or existing[identity] == sha, 'existing package/version differs')
        require(path.name not in names or names[path.name] == sha, 'existing filename differs')


def check_keys(colons, primary, subkey):
    records = []
    pending = None
    for line in colons.splitlines():
        fields = line.split(':')
        if fields[0] in ('sec', 'ssb'):
            require(len(fields) > 14, 'incomplete key record')
            pending = fields
        elif fields[0] == 'fpr' and pending is not None:
            records.append((pending[0], fields[9], pending[14], pending[11], pending[1]))
            pending = None
    require(len(records) == 2, 'expected exactly one primary and one secret subkey')
    require(records[0][0:3] == ('sec', primary, '#') and records[0][4] not in ('r', 'e', 'd'),
            'wrong/unusable primary or primary secret material present')
    require(records[1][0:3] == ('ssb', subkey, '+') and 's' in records[1][3]
            and records[1][4] not in ('r', 'e', 'd'), 'wrong/unusable signing subkey')


def sign(site, debs):
    primary = os.environ['APT_PRIMARY_FINGERPRINT'].upper()
    subkey = os.environ['APT_SIGNING_SUBKEY_FINGERPRINT'].upper()
    require(all(re.fullmatch(r'[0-9A-F]{40}', x) for x in (primary, subkey)) and primary != subkey, 'invalid fingerprints')
    # Only caller-created private files are removed; never touch a preexisting keyring.
    with tempfile.TemporaryDirectory(prefix='apt-sign-') as private:
        home = Path(private)
        home.chmod(0o700)
        env = {k: v for k, v in os.environ.items() if k not in ('APT_SIGNING_SUBKEY_B64', 'APT_SIGNING_PASSPHRASE', 'SOURCE_ACTIONS_READ_TOKEN', 'GH_TOKEN')}
        env['GNUPGHOME'] = str(home)
        key, password = home / 'key', home / 'password'
        try:
            key.write_bytes(base64.b64decode(os.environ['APT_SIGNING_SUBKEY_B64'], validate=True))
            password.write_text(os.environ['APT_SIGNING_PASSPHRASE'])
            key.chmod(0o600)
            password.chmod(0o600)
            subprocess.run(['gpg', '--batch', '--no-tty', '--import', str(key)], env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            key.unlink()
            colons = subprocess.check_output(['gpg', '--batch', '--with-colons', '--list-secret-keys'], env=env, stderr=subprocess.DEVNULL, text=True)
            check_keys(colons, primary, subkey)
            env['CERCANO_GPG_PASSPHRASE_FILE'] = str(password)
            subprocess.run(['bash', str(Path(__file__).with_name('build-apt-repo.sh').resolve()),
                            str(site.resolve()), str(debs.resolve()), subkey + '!'], env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            for signature in ('InRelease', 'Release.gpg'):
                args = ['gpg', '--batch', '--status-fd', '1', '--verify', str(site / 'dists/stable' / signature)]
                if signature == 'Release.gpg':
                    args.append(str(site / 'dists/stable/Release'))
                status = subprocess.check_output(args, env=env, stderr=subprocess.DEVNULL, text=True)
                require('[GNUPG:] VALIDSIG ' + subkey + ' ' in status, 'signature did not use exact subkey')
        finally:
            subprocess.run(['gpgconf', '--kill', 'all'], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def local_api():
    require(os.environ['GITHUB_REPOSITORY'] == 'cercano-ai/packages', 'wrong target repository')
    require(os.environ['GITHUB_REF'] == 'refs/heads/main' and os.environ['GITHUB_RUN_ATTEMPT'] == '1', 'main only; reruns require recovery')
    return API('cercano-ai/packages', os.environ['GH_TOKEN'])


def source_artifact(source, run_id, artifact_id, version, sha, expected):
    require(run_id.isdecimal() and artifact_id.isdecimal(), 'IDs must be numeric')
    require(re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', version), 'invalid version')
    require(re.fullmatch(r'[0-9a-f]{40}', sha), 'full source SHA required')
    run = source.request('/actions/runs/' + run_id)
    workflow = source.request('/actions/workflows/release-macos.yml')
    require(run['status'] == 'completed' and run['conclusion'] == 'success'
            and run['event'] == 'workflow_dispatch' and run['head_sha'] == sha
            and run['head_branch'] == 'main' and run['repository']['full_name'] == SOURCE
            and run['head_repository']['full_name'] == SOURCE
            and run['workflow_id'] == workflow['id'] and run['path'] == WORKFLOW, 'source provenance rejected')
    artifacts = source.pages('/actions/runs/' + run_id + '/artifacts', 'artifacts')
    matches = [a for a in artifacts if str(a['id']) == artifact_id]
    require(len(matches) == 1, 'artifact not in requested run')
    artifact = matches[0]
    require(not artifact['expired'] and artifact['name'] == f'cercano-{version}-linux-packages-review', 'wrong/expired artifact')
    require(re.fullmatch(r'[0-9a-f]{64}', expected) and artifact.get('digest') == 'sha256:' + expected, 'artifact API digest mismatch')
    # The artifact ZIP endpoint rejects Accept: application/octet-stream (HTTP 415); with the
    # standard media type it answers 302 to blob storage, which request() follows without the token.
    data = source.request('/actions/artifacts/' + artifact_id + '/zip', binary=True, accept='application/vnd.github+json')
    require(digest(data) == expected, 'download digest mismatch')
    return data, run


def prepare():
    api = local_api()
    base = os.environ['BASE']
    previous = history(api, base)
    site = Path('site')
    site.mkdir()
    if previous:
        metadata = json.loads(asset(api, previous, 'candidate.json'))
        unpack_snapshot(asset(api, previous, 'snapshot.tar.gz'), metadata['snapshot_sha256'], site)
    source = API(SOURCE, os.environ['SOURCE_ACTIONS_READ_TOKEN'])
    run_id, artifact_id = os.environ['SOURCE_RUN_ID'], os.environ['SOURCE_ARTIFACT_ID']
    version, sha, expected = (os.environ[k] for k in ('VERSION', 'SOURCE_SHA', 'ARTIFACT_SHA256'))
    data, run = source_artifact(source, run_id, artifact_id, version, sha, expected)
    debs = Path('debs')
    packages_from_zip(data, version, debs)
    immutable_pool(site, debs)
    # Never let old Release/signature files enter the newly generated Release index.
    for name in ('Release', 'Release.gpg', 'InRelease'):
        (site / 'dists/stable' / name).unlink(missing_ok=True)
    sign(site, debs)
    (site / '.nojekyll').touch()
    (site / 'index.html').write_text('<!doctype html><title>Cercano apt</title><p>Experimental Cercano apt repository.</p>\n')
    out = Path('candidate')
    out.mkdir()
    with tarfile.open(out / 'snapshot.tar.gz', 'w:gz') as archive:
        total = 0
        for path in sorted(site.rglob('*')):
            if path.is_file():
                total += path.stat().st_size
                require(total <= LIMIT and not path.is_symlink(), 'site too large or symlink')
                archive.add(path, arcname=str(path.relative_to(site)), recursive=False)
    tag = PREFIX + os.environ['GITHUB_RUN_ID']
    candidate = {'tag': tag, 'base': base, 'snapshot_sha256': digest((out / 'snapshot.tar.gz').read_bytes()),
                 'source_run': run_id, 'source_run_attempt': run['run_attempt'], 'source_sha': sha,
                 'source_artifact': artifact_id, 'artifact_sha256': expected, 'version': version,
                 'workflow_sha': os.environ['GITHUB_SHA'], 'helper_revision': HELPER_REVISION}
    payload = json.dumps(candidate, sort_keys=True).encode()
    (out / 'candidate.json').write_bytes(payload)
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write(f'tag={tag}\ncandidate_sha256={digest(payload)}\n')


def retain():
    api = local_api()
    candidate = json.loads(Path('candidate/candidate.json').read_bytes())
    require(digest(Path('candidate/candidate.json').read_bytes()) == os.environ['CANDIDATE_SHA256'], 'candidate metadata changed')
    require(candidate['tag'] == PREFIX + os.environ['GITHUB_RUN_ID'] and candidate['base'] == os.environ['BASE'], 'wrong candidate')
    history(api, candidate['base'])
    data = Path('candidate/snapshot.tar.gz').read_bytes()
    require(digest(data) == candidate['snapshot_sha256'], 'candidate snapshot changed')
    release = api.request('/releases', 'POST', {'tag_name': candidate['tag'], 'target_commitish': os.environ['GITHUB_SHA'],
        'name': candidate['tag'], 'body': 'CANDIDATE, not proof of publication. See published.json receipt.',
        'draft': False, 'prerelease': True, 'make_latest': 'false'})
    api.upload(release['id'], 'candidate.json', Path('candidate/candidate.json').read_bytes())
    api.upload(release['id'], 'snapshot.tar.gz', data)
    require(asset(api, release, 'snapshot.tar.gz') == data, 'durable snapshot read-back failed')
    require(asset(api, release, 'candidate.json') == Path('candidate/candidate.json').read_bytes(), 'durable metadata read-back failed')


def finalize():
    api = local_api()
    tag = PREFIX + os.environ['GITHUB_RUN_ID']
    release = api.request('/releases/tags/' + tag)
    payload = asset(api, release, 'candidate.json')
    require(digest(payload) == os.environ['CANDIDATE_SHA256'], 'candidate metadata changed')
    receipt = {'state': 'published', 'candidate_sha256': digest(payload),
               'page_url': os.environ['PAGE_URL'], 'run_id': os.environ['GITHUB_RUN_ID']}
    validate_receipt(receipt, digest(payload), tag)
    data = json.dumps(receipt, sort_keys=True).encode()
    api.upload(release['id'], 'published.json', data)
    require(asset(api, release, 'published.json') == data, 'receipt read-back failed')


if __name__ == '__main__':
    try:
        {'prepare': prepare, 'retain': retain, 'finalize': finalize}[sys.argv[1]]()
    except Exception:
        # Deliberately avoid exception payloads: HTTP errors and subprocess diagnostics can carry credentials.
        print('apt promotion failed closed; inspect stage and perform reviewed recovery (no reruns).', file=sys.stderr)
        sys.exit(1)
