# Local verification (2026-11-17)

- Regression-first run, before either code fix: 22 fixture tests ran with 16
  failures and 7 errors (subtests counted separately). The helper fixture observed
  an unwanted `binary-arm64` directory. Receipt negatives demonstrated acceptance
  of non-HTTPS/wrong-host/wrong-path URLs, non-string URLs, missing/mismatched or
  wrongly typed run IDs, and extra fields; malformed schemas raised incidental
  exceptions rather than deliberate receipt validation errors.
- `python3 -m unittest discover -s tests -v`: all 22 portable fixture tests passed
  after the fixes, including retained chain, stale-state and guard behavior.
  Coverage includes hostile archives, digest/inventory mismatch, pool conflicts,
  primary-secret and wrong-subkey rejection, wrong source run/workflow/branch/repo,
  artifact mismatch/expiry/tampering, package architectures/versions, stale base,
  incomplete release/receipt, broken chain, and main/rerun guards.
- `python3 -m py_compile scripts/apt.py tests/test_apt.py`: passed.
- `actionlint .github/workflows/apt-pages.yml`: passed.
- `shellcheck scripts/build-apt-repo.sh`: passed.
- `bash -n scripts/build-apt-repo.sh`: passed.
- `git diff --check`: passed.
- Tools used: Python 3.9.6 on macOS, actionlint 1.7.12, ShellCheck 0.11.0.
  This does not verify the README's Python 3.11+ Linux toolchain.
- Original upstream helper SHA256 (recorded by the prior source inspection and
  rechecked against the local pre-patch file in this run), revision
  `cf406792ecb487c9492a6bd259aa36b807b7f743`:
  `330ecf68baeaf7763e3e64f73e53086c42b1da4534f017fa53df06a3454c939e`.
  The local helper now has a two-line patch removing `arm64` from the index loop
  and Release architecture field. Its SHA256 is
  `07170703418943c76ae35607b15b6d28c6dcec40fff6d2b98f677ce35c59f607`.
  No upstream fetch was performed for this regression-fix run.

Local source review checked workflow permission separation, immutable action pins,
archive extraction, signature selection, and pending-candidate blocking. Additional
provenance/architecture negative tests were added during review; primary key
revocation/expiry/disable state is also rejected. This was **not an independent
review**; sub-agent delegation was unavailable under the execution constraints.

No `gpg`, `dpkg-deb`, `apt-ftparchive`, or `docker` was available locally. Therefore
no disposable GPG signing, real Debian package parsing, container build, apt client
installation, or GitHub Pages/Release transaction test was performed. Fixture key
records and mocked package identities do not establish those properties in Linux.
The toolchain recipe is unbuilt, and no production image digest is supplied. The
README acceptance gate must remain closed until its Linux and GitHub acceptance
steps and independent review are complete. No remote settings, secrets, releases,
or deployments were changed, and no branch was pushed.
