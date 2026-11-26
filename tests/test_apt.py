import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch, Mock, MagicMock
import zipfile

spec = importlib.util.spec_from_file_location('apt', Path(__file__).parents[1] / 'scripts/apt.py')
apt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(apt)


def zipped(files):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        for name, value in files:
            archive.writestr(name, value)
    return data.getvalue()


def package_zip(change=None):
    files = {f'{p}_1.2.3_amd64.deb': b'fixture-' + p.encode() for p in ('cercano', 'cercano-agent', 'cercano-cli')}
    sums = ''.join(apt.digest(data) + '  ./' + name + '\n' for name, data in files.items())
    files['SHA256SUMS'] = sums.encode()
    if change:
        change(files)
    return zipped(files.items())


class ArchiveTests(unittest.TestCase):
    def test_names(self):
        for name in ('../x', '/x', 'a/../x', './x', 'x//y', 'x\\y', 'x\ny', 'x:y'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                apt.safe_name(name)
        apt.safe_name('pool/main/c/cercano/a_1.2.3_amd64.deb')
        apt.safe_name('.nojekyll')

    @patch.object(apt, 'validate_debs', return_value=True)
    def test_valid_checksums(self, _):
        with tempfile.TemporaryDirectory() as temp:
            self.assertTrue(apt.packages_from_zip(package_zip(), '1.2.3', Path(temp) / 'debs'))

    def test_bad_archives(self):
        fixtures = [
            zipped([('../evil', b'x')]),
            zipped([('x', b'x'), ('x', b'y')]),
            package_zip(lambda f: f.update({'extra': b'x'})),
            package_zip(lambda f: f.update({'cercano_1.2.3_amd64.deb': b'tampered'})),
            package_zip(lambda f: f.update({'SHA256SUMS': b'not checksums'})),
            package_zip(lambda f: f.pop('cercano_1.2.3_amd64.deb')),
        ]
        link = zipfile.ZipInfo('link')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        fixtures.append(zipped([(link, '../target')]))
        for data in fixtures:
            with tempfile.TemporaryDirectory() as temp, self.assertRaises(ValueError):
                apt.packages_from_zip(data, '1.2.3', Path(temp) / 'debs')

    def test_expansion_limit(self):
        with patch.object(apt, 'LIMIT', 1), tempfile.TemporaryDirectory() as temp, self.assertRaises(ValueError):
            apt.packages_from_zip(package_zip(), '1.2.3', Path(temp) / 'debs')

    def test_snapshot(self):
        for kind in (tarfile.REGTYPE, tarfile.SYMTYPE, tarfile.LNKTYPE):
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode='w:gz') as archive:
                entry = tarfile.TarInfo('pool/file')
                entry.type = kind
                entry.size = 1 if kind == tarfile.REGTYPE else 0
                entry.linkname = '../../escape' if kind != tarfile.REGTYPE else ''
                archive.addfile(entry, io.BytesIO(b'x'))
            data = stream.getvalue()
            with tempfile.TemporaryDirectory() as temp:
                if kind == tarfile.REGTYPE:
                    apt.unpack_snapshot(data, apt.digest(data), Path(temp))
                    self.assertEqual((Path(temp) / 'pool/file').read_bytes(), b'x')
                else:
                    with self.assertRaises(ValueError):
                        apt.unpack_snapshot(data, apt.digest(data), Path(temp))
                with self.assertRaises(ValueError):
                    apt.unpack_snapshot(data, '0' * 64, Path(temp))

    @patch.object(apt, 'deb_identity', return_value=('cercano', '1.2.3', 'amd64'))
    def test_pool_immutability(self, _):
        with tempfile.TemporaryDirectory() as temp:
            site, debs = Path(temp) / 'site', Path(temp) / 'debs'
            (site / 'pool').mkdir(parents=True)
            debs.mkdir()
            (site / 'pool/old.deb').write_bytes(b'a')
            (debs / 'renamed.deb').write_bytes(b'b')
            with self.assertRaisesRegex(ValueError, 'version differs'):
                apt.immutable_pool(site, debs)
            (debs / 'renamed.deb').write_bytes(b'a')
            apt.immutable_pool(site, debs)


class BuildHelperTests(unittest.TestCase):
    def test_generated_architectures_match_validator(self):
        # Exercise the real helper with fixture tools; no signing or Debian tools.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tools, debs, home, site = [root / name for name in ('bin', 'debs', 'gnupg', 'site')]
            for directory in (tools, debs, home):
                directory.mkdir(mode=0o700)
            (debs / 'cercano_1.2.3_all.deb').write_bytes(b'fixture')
            stubs = {
                'stat': 'echo 700',
                'gpg': 'exit 0',
                'dpkg-deb': 'echo cercano',
                'apt-ftparchive': 'case "$*" in *release*) printf "%s\\n" "$@";; *) printf "Package: cercano\\nArchitecture: all\\nVersion: 1.2.3\\n\\n";; esac',
            }
            for name, body in stubs.items():
                tool = tools / name
                tool.write_text('#!/bin/bash\n' + body + '\n')
                tool.chmod(0o700)
            env = dict(os.environ, PATH=str(tools) + os.pathsep + os.environ['PATH'], GNUPGHOME=str(home))
            subprocess.run(['bash', str(Path(apt.__file__).with_name('build-apt-repo.sh')),
                            str(site), str(debs), 'B' * 40], env=env, check=True, capture_output=True)
            self.assertEqual({p.name for p in (site / 'dists/stable/main').iterdir()},
                             {'binary-amd64', 'binary-all'})
            release = (site / 'dists/stable/Release').read_text()
            self.assertIn('Architectures=amd64 all', release)
            self.assertNotIn('arm64', release)
            for arch in ('amd64', 'all'):
                self.assertIn('Architecture: all', (site / f'dists/stable/main/binary-{arch}/Packages').read_text())


def key_records(primary='A' * 40, subkey='B' * 40, primary_secret='#', sub_secret='+', cap='s', validity='u'):
    lines = []
    for kind, fpr, secret, caps in [('sec', primary, primary_secret, 'scSC'), ('ssb', subkey, sub_secret, cap)]:
        fields = [''] * 16
        fields[0], fields[1], fields[11], fields[14] = kind, validity, caps, secret
        lines.append(':'.join(fields))
        lines.append('fpr:::::::::' + fpr + ':')
    return '\n'.join(lines)


class KeyTests(unittest.TestCase):
    def test_subkey_only(self):
        apt.check_keys(key_records(), 'A' * 40, 'B' * 40)

    def test_reject_keys(self):
        for change in ({'primary_secret': '+'}, {'primary': 'C' * 40}, {'subkey': 'C' * 40},
                       {'sub_secret': '#'}, {'sub_secret': 'CARD'}, {'cap': 'e'}, {'validity': 'r'}, {'validity': 'e'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                apt.check_keys(key_records(**change), 'A' * 40, 'B' * 40)
        with self.assertRaises(ValueError):
            apt.check_keys(key_records() + '\n' + key_records(), 'A' * 40, 'B' * 40)


class FakeAPI:
    def __init__(self):
        self.releases = []
        self.assets = {}

    def pages(self, path):
        return self.releases

    def add(self, tag, base, published=True):
        release = {'id': len(self.releases) + 1, 'tag_name': tag, 'draft': False}
        self.releases.append(release)
        candidate = {'tag': tag, 'base': base}
        self.assets[tag, 'candidate.json'] = json.dumps(candidate, sort_keys=True).encode()
        if published:
            self.assets[tag, 'published.json'] = json.dumps({'state': 'published', 'page_url': 'https://cercano-ai.github.io/packages/',
                'run_id': tag.removeprefix('apt-snapshot-'), 'candidate_sha256': apt.digest(self.assets[tag, 'candidate.json'])}).encode()

    def asset(self, api, release, name):
        return self.assets[release['tag_name'], name]


class StateTests(unittest.TestCase):
    def test_first_and_published(self):
        api = FakeAPI()
        with patch.object(apt, 'asset', api.asset):
            self.assertIsNone(apt.history(api, 'EMPTY'))
            api.add('apt-snapshot-1', 'EMPTY')
            self.assertEqual(apt.history(api, 'apt-snapshot-1')['id'], 1)
            with self.assertRaisesRegex(ValueError, 'stale'):
                apt.history(api, 'EMPTY')

    def test_pending_blocks_even_if_older(self):
        api = FakeAPI()
        api.add('apt-snapshot-1', 'EMPTY', published=False)
        api.add('apt-snapshot-2', 'apt-snapshot-1')
        with patch.object(apt, 'asset', api.asset), self.assertRaises(KeyError):
            apt.history(api, 'apt-snapshot-2')

    def test_partial_release_blocks(self):
        api = FakeAPI()
        api.add('apt-snapshot-1', 'EMPTY')
        del api.assets['apt-snapshot-1', 'candidate.json']
        with patch.object(apt, 'asset', api.asset), self.assertRaises(KeyError):
            apt.history(api, 'apt-snapshot-1')

    def test_broken_chain_or_receipt_blocks(self):
        api = FakeAPI()
        api.add('apt-snapshot-1', 'not-empty')
        with patch.object(apt, 'asset', api.asset), self.assertRaisesRegex(ValueError, 'chain'):
            apt.history(api, 'apt-snapshot-1')
        api = FakeAPI()
        api.add('apt-snapshot-1', 'EMPTY')
        api.assets['apt-snapshot-1', 'published.json'] = b'{"state":"candidate","page_url":"url","candidate_sha256":"bad"}'
        with patch.object(apt, 'asset', api.asset), self.assertRaisesRegex(ValueError, 'receipt'):
            apt.history(api, 'apt-snapshot-1')

    def test_invalid_receipt_schema_url_and_run_binding(self):
        changes = [
            {'page_url': 'http://cercano-ai.github.io/packages/'},
            {'page_url': 'https://evil.test/packages/'},
            {'page_url': 'https://cercano-ai.github.io/other/'},
            {'page_url': 'https://cercano-ai.github.io/packages/?query=1'},
            {'page_url': 'https://cercano-ai.github.io/packages/#fragment'},
            {'page_url': 'https://cercano-ai.github.io@evil.test/packages/'},
            {'page_url': True}, {'page_url': ['https://cercano-ai.github.io/packages/']},
            {'run_id': '2'}, {'run_id': 1}, {'run_id': True}, {'run_id': '01'},
            {'run_id': 'not-a-run'}, {'extra': 'unexpected'},
        ]
        for change in changes + [{'remove': key} for key in ('run_id', 'page_url', 'state', 'candidate_sha256')]:
            api = FakeAPI()
            api.add('apt-snapshot-1', 'EMPTY')
            receipt = json.loads(api.assets['apt-snapshot-1', 'published.json'])
            if 'remove' in change:
                del receipt[change['remove']]
            else:
                receipt.update(change)
            api.assets['apt-snapshot-1', 'published.json'] = json.dumps(receipt).encode()
            with self.subTest(change=change), patch.object(apt, 'asset', api.asset), self.assertRaisesRegex(ValueError, 'receipt'):
                apt.history(api, 'apt-snapshot-1')

    def test_non_object_receipts(self):
        for receipt in (None, [], 'published', 1):
            api = FakeAPI()
            api.add('apt-snapshot-1', 'EMPTY')
            api.assets['apt-snapshot-1', 'published.json'] = json.dumps(receipt).encode()
            with self.subTest(receipt=receipt), patch.object(apt, 'asset', api.asset), self.assertRaisesRegex(ValueError, 'receipt'):
                apt.history(api, 'apt-snapshot-1')

    def test_linear_history_and_pages_url_forms(self):
        api = FakeAPI()
        api.add('apt-snapshot-1', 'EMPTY')
        api.add('apt-snapshot-2', 'apt-snapshot-1')
        receipt = json.loads(api.assets['apt-snapshot-2', 'published.json'])
        receipt['page_url'] = 'https://cercano-ai.github.io/packages'
        api.assets['apt-snapshot-2', 'published.json'] = json.dumps(receipt).encode()
        with patch.object(apt, 'asset', api.asset):
            self.assertEqual(apt.history(api, 'apt-snapshot-2')['id'], 2)

    def test_main_and_rerun_guard(self):
        env = {'GITHUB_REPOSITORY': 'cercano-ai/packages', 'GITHUB_REF': 'refs/heads/other', 'GITHUB_RUN_ATTEMPT': '1', 'GH_TOKEN': 'fake'}
        with patch.dict(os.environ, env), self.assertRaises(ValueError):
            apt.local_api()
        env.update(GITHUB_REF='refs/heads/main', GITHUB_RUN_ATTEMPT='2')
        with patch.dict(os.environ, env), self.assertRaises(ValueError):
            apt.local_api()



class RequestHeaderTests(unittest.TestCase):
    def fetch(self, path):
        seen = []
        def opener(*args):
            def open_(req, timeout):
                seen.append(req)
                raise apt.urllib.error.HTTPError(req.full_url, 302, 'Found', {'Location': 'https://blob.example/zip'}, None)
            return Mock(open=open_)
        body = MagicMock()
        body.read.return_value = b'zip'
        with patch.object(apt.urllib.request, 'build_opener', opener),              patch.object(apt.urllib.request, 'urlopen', Mock(return_value=body)) as blob:
            api = apt.API(apt.SOURCE, 'token')
            if path == 'artifact':
                self.data = b'zip'; self.sha = 'a' * 40
                run = dict(status='completed', conclusion='success', event='workflow_dispatch', head_sha=self.sha,
                           head_branch='main', repository={'full_name': apt.SOURCE},
                           head_repository={'full_name': apt.SOURCE}, workflow_id=12, path=apt.WORKFLOW)
                api_json = {'/actions/runs/1': run, '/actions/workflows/release-macos.yml': {'id': 12}}
                real = api.request
                api.request = lambda p, **kw: api_json[p] if p in api_json else real(p, **kw)
                api.pages = lambda *a: [dict(id=2, name='cercano-1.2.3-linux-packages-review', expired=False,
                                             digest='sha256:' + apt.digest(b'zip'))]
                apt.source_artifact(api, '1', '2', '1.2.3', self.sha, apt.digest(b'zip'))
            else:
                api.request('/releases/assets/7', binary=True)
        # The redirect target is fetched as a bare URL, so the API token never reaches blob storage.
        self.assertEqual(blob.call_args.args[0], 'https://blob.example/zip')
        return seen[0]

    def test_artifact_zip_uses_standard_media_type(self):
        self.assertEqual(self.fetch('artifact').get_header('Accept'), 'application/vnd.github+json')

    def test_release_asset_uses_octet_stream(self):
        self.assertEqual(self.fetch('asset').get_header('Accept'), 'application/octet-stream')


class ProvenanceTests(unittest.TestCase):
    def source(self):
        self.data = b'fixture ZIP bytes'
        self.sha = 'a' * 40
        self.run = dict(status='completed', conclusion='success', event='workflow_dispatch',
                        head_sha=self.sha, head_branch='main', repository={'full_name': apt.SOURCE},
                        head_repository={'full_name': apt.SOURCE}, workflow_id=12, path=apt.WORKFLOW)
        self.artifact = dict(id=2, name='cercano-1.2.3-linux-packages-review', expired=False,
                             digest='sha256:' + apt.digest(self.data))
        api = Mock()
        api.request.side_effect = lambda path, **kw: {'/actions/runs/1': self.run,
            '/actions/workflows/release-macos.yml': {'id': 12}, '/actions/artifacts/2/zip': self.data}[path]
        api.pages.side_effect = lambda *args: [self.artifact]
        return api

    def download(self, api, version='1.2.3'):
        return apt.source_artifact(api, '1', '2', version, self.sha, apt.digest(b'fixture ZIP bytes'))

    def test_success(self):
        api = self.source()
        self.assertEqual(self.download(api)[0], self.data)

    def test_wrong_run_provenance(self):
        for field, bad in [('status', 'in_progress'), ('conclusion', 'failure'), ('event', 'pull_request'),
                           ('head_sha', 'b' * 40), ('head_branch', 'evil'), ('workflow_id', 99),
                           ('path', '.github/workflows/other.yml'), ('repository', {'full_name': 'fork/Cercano'}),
                           ('head_repository', {'full_name': 'fork/Cercano'})]:
            api = self.source()
            self.run[field] = bad
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.download(api)

    def test_wrong_artifact(self):
        for field, bad in [('id', 9), ('name', 'other'), ('expired', True), ('digest', None)]:
            api = self.source()
            self.artifact[field] = bad
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.download(api)
        api = self.source()
        self.data = b'tampered'
        with self.assertRaisesRegex(ValueError, 'download digest'):
            self.download(api)

    def test_stable_versions_only(self):
        for version in ('1.2.3-beta', '1.2.3+build', '01.2.3', '../1.2.3', 'v1.2.3'):
            with self.subTest(version=version), self.assertRaises(ValueError):
                self.download(self.source(), version)


class IdentityTests(unittest.TestCase):
    def test_architecture_and_version(self):
        good = [('cercano', '1.2.3', 'all'), ('cercano-agent', '1.2.3', 'amd64'),
                ('cercano-cli', '1.2.3', 'amd64')]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for i in range(3):
                (root / f'{i}.deb').touch()
            with patch.object(apt, 'deb_identity', side_effect=good):
                apt.validate_debs(root, '1.2.3')
            for bad in [('cercano', '1.2.3', 'amd64'), ('cercano', '1.2.4', 'all')]:
                with patch.object(apt, 'deb_identity', side_effect=[bad] + good[1:]), self.assertRaises(ValueError):
                    apt.validate_debs(root, '1.2.3')

if __name__ == '__main__':
    unittest.main()
