# Local verification (2026-11-17)

- `python3 -m unittest discover -s tests -v`: 18 portable fixture tests passed.
  Coverage includes hostile archives, digest/inventory mismatch, pool conflicts,
  primary-secret and wrong-subkey rejection, wrong source run/workflow/branch/repo,
  artifact mismatch/expiry/tampering, package architectures/versions, stale base,
  incomplete release/receipt, broken chain, and main/rerun guards.
- `python3 -m py_compile scripts/apt.py tests/test_apt.py`: passed.
- `actionlint .github/workflows/apt-pages.yml`: passed.
- `shellcheck scripts/build-apt-repo.sh`: passed.
- `git diff --check`: passed.
- Vendored build helper SHA256 matches the file fetched read-only from upstream
  `cf406792ecb487c9492a6bd259aa36b807b7f743`:
  `330ecf68baeaf7763e3e64f73e53086c42b1da4534f017fa53df06a3454c939e`.

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
