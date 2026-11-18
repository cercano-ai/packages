# Synthetic signing/APT integration — 2026-11-17

This is **synthetic integration only**, not real Cercano package acceptance.
`APT_REAL_PACKAGE_ACCEPTED` remains unset; no production keys, GitHub settings,
publication, pushes, merges, releases or dispatches were used.

## Reproduce (disposable Linux amd64 container only)

From this worktree, explicitly select the isolated context on every Docker call:

```sh
IMAGE=sha256:68700264907a35070e16f34d2f94a7b044688a4a145f887f30f26575297a892f
docker --context colima-cercano-apt image inspect "$IMAGE" \
  --format '{{.Id}} {{.Os}}/{{.Architecture}}'
docker --context colima-cercano-apt run --rm --platform linux/amd64 \
  --network none --mount "type=bind,source=$PWD,target=/src,readonly" -w /src \
  "$IMAGE" sh -ec 'python3 -B tests/integration_apt.py; python3 -B -m unittest discover -s tests -v; PYTHONPYCACHEPREFIX=/tmp/pycache python3 -m py_compile scripts/apt.py tests/test_apt.py tests/test_toolchain.py tests/test_integration_apt.py tests/integration_apt.py; bash -n scripts/build-apt-repo.sh'
```

The image inspect command returned exit 0 and the exact image ID above,
`linux/amd64`. The final combined command returned **exit 0** in 19.774 seconds:
all integration assertions passed, all **28 portable fixtures** passed, compilation
and shell syntax checks passed. The fixture duplicate-ZIP warning is intentional.
The image was already local; no pull/build/runtime dependency installation occurred.
The container had no network, production credentials, host GPG mounts or writable
source mounts. Only its disposable filesystem was written during execution.
Each subprocess is bounded to 90 seconds; the recorded Docker invocation was
bounded externally to 300 seconds. Run only in a disposable container: the positive
case really installs packages into its root dpkg database.

## Assertions and observed results

`tests/integration_apt.py` calls the unmodified real `scripts/apt.py::sign()` and
vendored `build-apt-repo.sh`. Its observer wraps subprocess execution, but forwards
every command to the real subprocess implementation; it does not fake GPG, dpkg,
APT, helper output or key records.

- Generates two fresh password-protected RSA2048 certification primaries, each with
  one password-protected signing subkey, expiring in one day. Exports an exact
  subkey-only secret for signing; full-primary secret export is a negative fixture.
- Builds three actual minimal `.deb` files with `dpkg-deb`, version `0.0.1`:
  `cercano-cli`/`cercano-agent` amd64 and `cercano` all, with exact metapackage
  dependencies. Payloads are plainly labelled synthetic, with no maintainer scripts.
  `validate_debs()` accepts their controls.
- Successful signing verifies exact `SUBKEY!` helper selection and the real imported
  secret-key records. Independent `gpgv` verifies both InRelease and detached
  Release.gpg against separately exported public material, asserting exactly the
  generated signing fingerprint and primary-fingerprint field in the sole, complete
  VALIDSIG record (not an unrelated occurrence elsewhere in status). Exported repository keyring
  equals that public material; Release and indices contain amd64/all as expected.
- Wrong passphrase and forced helper failure (empty input directory) raise real
  `CalledProcessError` from bash, with captured stderr containing respectively the
  exact diagnostic line `gpg: signing failed: Bad passphrase` or
  `error: no deb packages`. Captured stderr is never included in assertion output.
  The successful signing and APT install remain positive controls.
  Wrong primary fingerprint, wrong signing-subkey
  fingerprint, a different imported key and full-primary secret import raise the
  exact intended key-validation messages, not arbitrary incidental exceptions.
- For all seven sign attempts: private homes mode 0700, key/password files 0600,
  imported key file removed before helper execution, signing/token environment
  credentials excluded from children, `gpgconf --kill all` exit 0 and agent sockets
  absent afterward, then complete home/key/password removal. An ambient GNUPGHOME
  sentinel remains unchanged; it is never used by signing subprocesses.
- APT uses only a fresh per-case source list/state/cache with a local `file:` URL and
  explicit `signed-by`. No insecure or trusted=yes settings. A different keyring
  fails update with NO_PUBKEY. Altering signed InRelease fails independent gpgv
  (BAD signature) and APT update (BADSIG).
- A same-size, one-byte mutation of a pool `.deb` keeps signed metadata untouched:
  update succeeds, then download-only install fails with Hash Sum mismatch. Separate
  fresh caches prevent previously downloaded bytes masking the failure.
- Untampered repository update and actual install succeed for all three packages;
  dpkg status/version/architecture and installed synthetic payloads match exactly.

Passing integration output (final run):

```text
PASS good: real sign/import, isolated home/credentials removed, agent sockets removed
PASS wrong-passphrase: real sign/import, isolated home/credentials removed, agent sockets removed
PASS wrong-fingerprint: real sign/import, isolated home/credentials removed, agent sockets removed
PASS wrong-subkey-fingerprint: real sign/import, isolated home/credentials removed, agent sockets removed
PASS wrong-imported-key: real sign/import, isolated home/credentials removed, agent sockets removed
PASS primary-secret: real sign/import, isolated home/credentials removed, agent sockets removed
PASS helper-failure: real sign/import, isolated home/credentials removed, agent sockets removed
PASS independent gpgv: both signatures exact signing subkey and primary; amd64/all indices
PASS tampered signed metadata and wrong signed-by key rejected by APT
PASS tampered package payload rejected against authenticated index
PASS signed-by file: APT update/install all three synthetic packages (Debian amd64 only)
PASS synthetic integration only; NOT APT_REAL_PACKAGE_ACCEPTED
```

## Regression-first diagnosis and limits

Review follow-up starting at `a78997aa35c8ae898635543f1d5f0c1cdcf66b9f`:
before editing, standalone Python reproductions showed the old assertions accepted
(1) a VALIDSIG with the wrong primary field when the expected primary appeared only
in an unrelated NEWSIG line, and (2) a bash CalledProcessError caused by an unrelated
command-not-found error. Both reproductions exited 0, confirming the false positives.
A disposable network-disabled run of the original harness, instrumented in memory
only to capture helper stderr, exited 0 in 18.838 seconds and observed the two exact
diagnostic lines above (GPG also emitted a clear-sign failure line). No passphrase
value was printed. This probe preceded all file edits.

The strengthened harness parses the sole VALIDSIG record and compares both fields;
it captures real helper stderr and checks cause-specific lines without printing it.
Five new portable regression methods cover valid status and diagnostic positive
controls, unrelated-primary rejection, wrong signing fingerprint, missing/duplicate/
incomplete VALIDSIG records, and unrelated or absent helper stderr rejection for
both expected causes. The full pinned-image command above then passed integration,
28 unit fixtures, compilation and shell syntax checks. No production implementation
changed. The following paragraph records the earlier harness-development history,
not the timing or fixture count of this follow-up:

Initial standalone harness execution exited 1 in 11.076 seconds: its subprocess
observer supplied `timeout` twice when real `check_output()` forwarded `timeout=None`.
The smallest fix was to set the observer kwargs timeout to 90 before forwarding.
No production code changed. The second standalone run exited 0 in 18.598 seconds.
An additional wrong-subkey-fingerprint assertion was then added, followed by the
successful complete command above. No production implementation defect reproduced.
Final `git diff --check` exited 0; the production signer/helper diff was empty.

This does not exercise real upstream artifacts/provenance, real CLI/agent behavior,
Ubuntu, retention upgrades, expired/revoked/extra key variants, detached-signature
fallback in APT, malicious maintainer scripts, power-loss cleanup, privileged
attackers or GitHub approval/Release/Pages transactions. Socket removal and
successful gpgconf shutdown are checked; this is not a system-wide process audit.
Private material exists only within the disposable container and is never printed;
Python in-memory copies are not explicitly zeroized. The README's full real-package
and independent deployment acceptance gate remains required. Earlier verification
notes are historical and are not retroactively claims of these tests.

Worktree retained at
`/Users/bryancostanich/git_repos/cercano-ai/Cercano/scratch/packages-toolchain-snapshot-fix`,
branch `fix/apt-toolchain-snapshot`; starting HEAD
`a78997aa35c8ae898635543f1d5f0c1cdcf66b9f` for this review follow-up. Git inspection was local: this bounded
sub-agent was not permitted further delegation. No remote Git operations occurred.
