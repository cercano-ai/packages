# Local amd64 snapshot compatibility validation

Validated 2026-11-17 using only Docker context `colima-cercano-apt`; the global
context remained `default`. No image was published or production setting changed.

## Pin evidence

- Unchanged base index digest:
  `sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc`.
- Platform-specific base image ID:
  `sha256:c67e92c8099ba22853ab4cfda7cfb494d253d6012ca82dee98ba8976772af018`.
- Base reported Debian 12.15, `x86_64`/`amd64`, `apt 2.6.2`,
  `perl-base 5.36.0-7+deb12u4`, and keyring `2023.3+deb12u2`.
- Base `debian.sources` records `20261104T212339Z` for both archives.
  Signed `apt-get update` against those snapshot URLs succeeded. `apt-cache policy`
  confirmed security snapshot candidates `apt-utils 2.6.2` and
  `perl 5.36.0-7+deb12u4`; installed apt/perl-base candidates were unchanged.
- Simulation of the recipe's full package list succeeded: 0 upgraded,
  56 newly installed, 0 removed, 0 not upgraded; no downgrades or bypass flags.

## Actual build and tests

Commands are documented in README under "Toolchain base/snapshot compatibility".
The build used `--platform linux/amd64`, the modified `toolchain` build context,
`--iidfile` and the local tag `cercano-apt-toolchain:local-amd64`.

- Build exit 0; resulting local image ID:
  `sha256:68700264907a35070e16f34d2f94a7b044688a4a145f887f30f26575297a892f`.
  Docker inspect reports `linux/amd64`; actual execution reports `x86_64`/`amd64`.
  This is a local image ID, **not a published registry manifest digest**.
- Resulting package pairs match: apt/apt-utils `2.6.2`,
  perl/perl-base `5.36.0-7+deb12u4`. Python is 3.11.2; `apt-get check` exited 0.
- Exact image ID, source mounted read-only, networking disabled:
  `python3 -B -m unittest discover -s tests -v` passed **23 tests**.
  `PYTHONPYCACHEPREFIX=/tmp/pycache python3 -m py_compile scripts/apt.py tests/test_apt.py tests/test_toolchain.py`
  exited 0. The duplicate-ZIP-name warning is from the intentional hostile fixture.
- Negative regression: in a disposable container copy only, reverting both snapshot
  timestamps to January 2025 made `test_verified_base_snapshot_pair` fail as
  expected. The source checkout was not modified for this check.
- `git diff --check` passed. Original feature checkout remained clean and unchanged.

Raw evidence is retained in the enclosing Cercano checkout at
`scratch/cercano-apt-linux-evidence/snapshot-fix/`: `base-inspect.json`,
`base-metadata.txt`, `snapshot-probe.log`, `build.log`, `image.iid`,
`image-inspect.json`, `image-metadata.txt`, `toolchain-packages.txt`, `tests.log`,
and `regression-negative.log`.

## Limitations

Execution was amd64 under the isolated Apple Silicon Colima/Rosetta runtime, not a
native amd64 host or arm64 acceptance run. Docker warned that buildx is absent and
used its legacy builder; this did not prevent the build. Independent reviewer and
delegated Git workflow were unavailable to this bounded agent; worktree creation,
diff inspection and checkpoint were performed locally, without an independent
review claim. No actionlint/shellcheck run, image vulnerability scan, real-package
installation/smoke acceptance, GitHub transaction validation, production key
access, push, merge, publication, configuration change, or release dispatch was
performed. This fixes the dependency pin mismatch, not the separate release gates.
