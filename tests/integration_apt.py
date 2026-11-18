#!/usr/bin/env python3
"""Opt-in, synthetic-only Linux container integration; never use production keys."""
import base64
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('apt_under_test', Path(__file__).resolve().parents[1] / 'scripts/apt.py')
apt = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(apt)
REAL_RUN = subprocess.run


def run(args, *, expected=0, **kwargs):
    result = REAL_RUN([str(a) for a in args], stdout=subprocess.PIPE,
                      stderr=subprocess.STDOUT, timeout=90, **kwargs)
    if (result.returncode == 0) != (expected == 0):
        raise AssertionError(f'{args[0:3]} exit={result.returncode}\n{result.stdout.decode(errors="replace")}')
    return result.stdout


def main():
    assert os.geteuid() == 0 and os.uname().machine == 'x86_64'
    with tempfile.TemporaryDirectory(prefix='synthetic-apt-') as tmp:
        root = Path(tmp)
        root.chmod(0o755)  # file: transport must be readable by _apt
        homes = []
        def keypair(name):
            home = root / name
            home.mkdir(mode=0o700)
            homes.append(home)
            cmd = ['gpg', '--homedir', home, '--batch', '--pinentry-mode', 'loopback', '--passphrase', 'disposable-only']
            run(cmd + ['--quick-generate-key', f'{name} <{name}@invalid.test>', 'rsa2048', 'cert', '1d'])
            listing = run(['gpg', '--homedir', home, '--with-colons', '--list-keys']).decode()
            primary = next(x.split(':')[9] for x in listing.splitlines() if x.startswith('fpr:'))
            run(cmd + ['--quick-add-key', primary, 'rsa2048', 'sign', '1d'])
            listing = run(['gpg', '--homedir', home, '--with-colons', '--list-keys']).decode()
            subkey = [x.split(':')[9] for x in listing.splitlines() if x.startswith('fpr:')][1]
            secret = run(cmd + ['--export-secret-subkeys', subkey + '!'])
            full = run(cmd + ['--export-secret-keys', primary])
            public = run(['gpg', '--homedir', home, '--export', primary])
            return primary, subkey, secret, full, public

        try:
            primary, subkey, secret, full, public = keypair('signer')
            other_primary, other_subkey, other_secret, _, other_public = keypair('other')
            debs = root / 'debs'
            debs.mkdir()
            for name, arch in [('cercano-cli', 'amd64'), ('cercano-agent', 'amd64'), ('cercano', 'all')]:
                package = root / name
                (package / 'DEBIAN').mkdir(parents=True)
                dependency = 'Depends: cercano-cli (= 0.0.1), cercano-agent (= 0.0.1)\n' if name == 'cercano' else ''
                (package / 'DEBIAN/control').write_text(f'Package: {name}\nVersion: 0.0.1\nArchitecture: {arch}\nMaintainer: Synthetic <test@invalid.test>\n{dependency}Description: disposable synthetic fixture, not Cercano\n')
                payload = package / 'usr/share/synthetic-apt' / name
                payload.parent.mkdir(parents=True)
                payload.write_text('synthetic fixture only\n')
                run(['dpkg-deb', '--build', '--root-owner-group', package, debs / f'{name}_0.0.1_{arch}.deb'])
            apt.validate_debs(debs, '0.0.1')
            ambient = root / 'ambient'
            ambient.mkdir(mode=0o700)
            (ambient / 'sentinel').write_text('untouched')
            baseline = dict(os.environ, GNUPGHOME=str(ambient),
                            APT_PRIMARY_FINGERPRINT=primary, APT_SIGNING_SUBKEY_FINGERPRINT=subkey,
                            APT_SIGNING_SUBKEY_B64=base64.b64encode(secret).decode(),
                            APT_SIGNING_PASSPHRASE='disposable-only',
                            SOURCE_ACTIONS_READ_TOKEN='synthetic-credential', GH_TOKEN='synthetic-token')

            def signing(label, changes=None, failure=None, inputs=debs,
                        message='wrong/unusable primary or primary secret material present'):
                seen = set()
                killed = set()
                def observe(args, **kwargs):
                    env = kwargs.get('env', {})
                    home = Path(env['GNUPGHOME'])
                    seen.add(home)
                    assert home != ambient and home.stat().st_mode & 0o777 == 0o700
                    assert not any(k in env for k in ('APT_SIGNING_SUBKEY_B64', 'APT_SIGNING_PASSPHRASE', 'SOURCE_ACTIONS_READ_TOKEN', 'GH_TOKEN'))
                    for filename in ('key', 'password'):
                        if (home / filename).exists():
                            assert (home / filename).stat().st_mode & 0o777 == 0o600
                    if args[0] == 'bash':
                        assert args[-1] == subkey + '!'
                        assert not (home / 'key').exists()
                    kwargs['timeout'] = 90
                    result = REAL_RUN(args, **kwargs)
                    if args[0] == 'gpgconf':
                        assert result.returncode == 0
                        assert not list(home.glob('S.gpg-agent*'))
                        killed.add(home)
                    return result
                site = root / label
                with patch.dict(os.environ, dict(baseline, **(changes or {})), clear=True), patch.object(apt.subprocess, 'run', observe):
                    try:
                        apt.sign(site, inputs)
                    except Exception as exc:
                        assert failure and isinstance(exc, failure), (label, type(exc))
                        if isinstance(exc, ValueError):
                            assert str(exc) == message, str(exc)
                        else:
                            assert exc.cmd[0] == 'bash', exc.cmd
                    else:
                        assert failure is None, f'{label} unexpectedly accepted'
                assert seen and seen == killed and all(not p.exists() for p in seen)
                assert list(ambient.iterdir()) == [ambient / 'sentinel']
                assert (ambient / 'sentinel').read_text() == 'untouched'
                print(f'PASS {label}: real sign/import, isolated home/credentials removed, agent sockets removed', flush=True)
                return site

            site = signing('good')
            signing('wrong-passphrase', {'APT_SIGNING_PASSPHRASE': 'incorrect'}, subprocess.CalledProcessError)
            signing('wrong-fingerprint', {'APT_PRIMARY_FINGERPRINT': other_primary}, ValueError)
            signing('wrong-subkey-fingerprint', {'APT_SIGNING_SUBKEY_FINGERPRINT': other_subkey},
                    ValueError, message='wrong/unusable signing subkey')
            signing('wrong-imported-key', {'APT_SIGNING_SUBKEY_B64': base64.b64encode(other_secret).decode()}, ValueError)
            signing('primary-secret', {'APT_SIGNING_SUBKEY_B64': base64.b64encode(full).decode()}, ValueError)
            empty = root / 'empty'
            empty.mkdir()
            signing('helper-failure', failure=subprocess.CalledProcessError, inputs=empty)
            verify_home = root / 'verify'
            verify_home.mkdir(mode=0o700)
            trust = root / 'trusted.gpg'
            trust.write_bytes(public)
            wrong_trust = root / 'wrong.gpg'
            wrong_trust.write_bytes(other_public)
            assert (site / 'cercano.gpg').read_bytes() == public
            for name in ('InRelease', 'Release.gpg'):
                args = ['gpgv', '--homedir', verify_home, '--keyring', trust, '--status-fd', '1', site / 'dists/stable' / name]
                if name == 'Release.gpg':
                    args.append(site / 'dists/stable/Release')
                status = run(args).decode()
                valid = [x.split()[2] for x in status.splitlines() if x.startswith('[GNUPG:] VALIDSIG ')]
                assert valid == [subkey], status
                assert primary in status
            assert 'Architectures: amd64 all' in (site / 'dists/stable/Release').read_text()
            assert 'Package: cercano\n' in (site / 'dists/stable/main/binary-all/Packages').read_text()
            print('PASS independent gpgv: both signatures exact signing subkey and primary; amd64/all indices', flush=True)

            def apt_client(label, repo, keyring):
                client = root / ('client-' + label)
                (client / 'lists/partial').mkdir(parents=True)
                (client / 'archives/partial').mkdir(parents=True)
                sources = client / 'sources.list'
                sources.write_text(f'deb [signed-by={keyring}] file:{repo} stable main\n')
                return ['apt-get', '-o', f'Dir::Etc::sourcelist={sources}', '-o', 'Dir::Etc::sourceparts=-',
                        '-o', f'Dir::State::lists={client}/lists', '-o', f'Dir::Cache::archives={client}/archives',
                        '-o', 'Acquire::Languages=none', '-o', 'APT::Get::List-Cleanup=0']

            bad_metadata = root / 'bad-metadata'
            shutil.copytree(site, bad_metadata)
            signed = bad_metadata / 'dists/stable/InRelease'
            signed.write_bytes(signed.read_bytes().replace(b'Origin: Cercano', b'Origin: Altered'))
            output = run(['gpgv', '--homedir', verify_home, '--keyring', trust, signed], expected=1)
            assert b'BAD signature' in output
            output = run(apt_client('metadata', bad_metadata, trust) + ['update'], expected=1)
            assert b'BADSIG' in output, output
            output = run(apt_client('trust', site, wrong_trust) + ['update'], expected=1)
            assert b'NO_PUBKEY' in output, output
            print('PASS tampered signed metadata and wrong signed-by key rejected by APT', flush=True)
            bad_payload = root / 'bad-payload'
            shutil.copytree(site, bad_payload)
            target = next(bad_payload.glob('pool/**/cercano-cli_*.deb'))
            data = bytearray(target.read_bytes())
            data[-1] ^= 1
            target.write_bytes(data)
            client = apt_client('payload', bad_payload, trust)
            run(client + ['update'])
            output = run(client + ['--download-only', '-y', 'install', 'cercano'], expected=1)
            assert b'Hash Sum mismatch' in output, output
            print('PASS tampered package payload rejected against authenticated index', flush=True)
            client = apt_client('good', site, trust)
            run(client + ['update'])
            run(client + ['-y', '--no-install-recommends', 'install', 'cercano'])
            for name in ('cercano', 'cercano-cli', 'cercano-agent'):
                assert run(['dpkg-query', '-W', '-f=${Status} ${Version} ${Architecture}', name]).decode() == 'install ok installed 0.0.1 ' + ('all' if name == 'cercano' else 'amd64')
                assert (Path('/usr/share/synthetic-apt') / name).read_text() == 'synthetic fixture only\n'
            print('PASS signed-by file: APT update/install all three synthetic packages (Debian amd64 only)', flush=True)
        finally:
            for home in homes:
                run(['gpgconf', '--homedir', home, '--kill', 'all'])
    print('PASS synthetic integration only; NOT APT_REAL_PACKAGE_ACCEPTED', flush=True)


if __name__ == '__main__':
    main()
