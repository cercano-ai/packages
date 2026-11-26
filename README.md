# Protected experimental Cercano apt repository

This is **local deployment preparation**, not an enabled or production-accepted service.
Nothing in this change creates environments, secrets, Pages settings, images or releases.
Only Debian/Ubuntu amd64 and arm64 plus the `all` metapackage are accepted: each
release brings the amd64 components and the metapackage, and optionally both arm64
components (never one without the other). Upstream still labels
Linux experimental; signing metadata does not turn Linux into a supported platform.

## Design and trust boundary

`.github/workflows/apt-pages.yml` is manual-only, main-only and refuses reruns. One
workflow-wide `apt-production` concurrency lock (no cancellation) covers approvals,
preparation, release retention, Pages deployment and the publication receipt. GitHub
may replace an older **pending** run with a newer pending run; this is not a FIFO queue.

1. Operator identifies a successful manual **main** run of
   `cercano-ai/Cercano/.github/workflows/release-macos.yml`, full source commit, exact
   `cercano-VERSION-linux-packages-review` artifact ID and its ZIP SHA256 from the API.
   The source repository and workflow cannot be overridden. API provenance, run
   conclusion, artifact membership/name/expiry/digest, downloaded digest, ZIP paths,
   member types, duplicates, size budget, complete `SHA256SUMS` inventory and Debian
   package identities are checked. No artifact contents are executed or installed.
   Only the `.deb` files (three, or five with arm64) are extracted; checksums cover
   the other formats too.
2. Preparation restores the last published full site from Releases, checks its digest,
   and rejects unsafe TAR members. It checks the *entire* retained pool for conflicting
   package/version/architecture or filenames before adding new packages. Exact repeats
   are allowed. All historical pool files remain downloadable and indexed.
3. The protected `apt-production` job imports only an exact, exportable signing subkey
   into a private temporary GNUPGHOME. Primary secret material, extra keys/subkeys,
   wrong fingerprints, non-signing, unavailable, revoked and expired subkeys are rejected.
   Both generated signatures must verify with the exact configured subkey. Secret and
   passphrase files are caller-owned, private, and removed in `finally`; the private
   agent is stopped. Nothing logs GPG diagnostics or secret contents. GitHub-hosted
   ephemeral runners are required: process kill/host loss cannot guarantee a finally
   block, and persistent/self-hosted runners are outside this design.
4. A separate `contents: write` job creates a prerelease tagged
   `apt-snapshot-RUN_ID`, uploads `candidate.json` and `snapshot.tar.gz`, then reads
   both back. Failure stops deployment. The retained snapshot includes **all** pool
   files and signed metadata, not just the newest packages. Actions artifacts are only
   temporary transport, not durable state.
5. The Pages job has **only** `pages: write` and `id-token: write`; no checkout,
   signing secrets, source credential, or release-storage permission. It uses the
   protected `github-pages` environment and the official SHA-pinned deploy action.
6. Only after the deploy action reports success does a separate release-write job add
   `published.json` binding the candidate digest, successful workflow run and Pages URL.
   Receipts have exactly four string fields: `state` (`published`), `candidate_sha256`
   (64 lowercase hex), `run_id` (positive decimal string matching the snapshot tag),
   and `page_url` (`https://cercano-ai.github.io/packages/`, with optional trailing slash).
   History and finalization enforce this format; custom domains are not accepted.
   The action does not expose a deployment ID; we deliberately do not invent one.
   A release title/prerelease flag alone never means published. Every snapshot must
   have a valid receipt and a linear parent chain. Any incomplete candidate, stale
   expected base, broken chain or uncertain state blocks the next promotion.

Source inspection was against remote Cercano main
`cf406792ecb487c9492a6bd259aa36b807b7f743`: packaging workflow, `package-verified.sh`,
`render_packages.py`, `build-apt-repo.sh`, `sign-apt-repo.sh`, and Linux artifact docs.
`scripts/build-apt-repo.sh` is vendored unmodified from that revision (SHA256
`330ecf68baeaf7763e3e64f73e53086c42b1da4534f017fa53df06a3454c939e`, unchanged on
Cercano main through `c5b7305c`). It indexes `amd64`, `arm64` and `all`, matching the
validator. Until a promoted release carries arm64 components, `binary-arm64` lists
only the `all` metapackage, whose dependencies an arm64 client cannot satisfy yet.
(An earlier local patch removed `arm64`; it was dropped when the validator gained arm64.)
The wrapper adds stricter primary/subkey checks, exact signature verification and
caller-file cleanup. No upstream helper is downloaded or executed dynamically.

**Trusted actors:** protected-main maintainers, environment reviewers, GitHub,
Debian's signed snapshot archive, and administrators controlling Releases, repository
variables and the toolchain image. Release assets are not inherently immutable and
GitHub does not offer a transaction spanning Releases and Pages. Restrict external
release edits/deletions and Pages deployments; never run another publisher. This
workflow cannot detect a privileged actor rewriting all history or an out-of-band
Pages deployment. No checksum substitutes for reviewing the source commit/build.

## Administrator setup — separate, explicit authorization required

Do not configure any of this merely to test the code. Merge a reviewed commit to
protected `main` before the first approved use. Require PR review for workflow,
scripts and toolchain changes; restrict direct pushes and administrator bypass.

### Environments and Pages

Create **both** `apt-production` and `github-pages` with:

- Required independent reviewers (verify the repository plan supports the rule).
- **Prevent self-review** enabled; dispatching actor must not approve their own run.
- Deployment branch policy restricted to **main only**, not all protected branches.
- Administrator/protection-rule bypass **disabled** wherever the plan permits it.
  If these controls are unavailable, do not use this workflow for production.

YAML naming an environment does **not** create these protections. Validate settings
in the UI/API with a second administrator. Only after separate authorization choose
GitHub Actions as the Pages source. Do not enable branch-based Pages publishing.
No custom domain is assumed. A project Pages URL normally includes `/packages/`.

### Secrets (three total, in `apt-production` only)

| Secret | Value |
| --- | --- |
| `APT_SIGNING_SUBKEY_B64` | Single-line standard base64 of a subkey-only GPG export, containing the public primary and **exactly one** secret signing subkey, no primary secret and no unrelated subkeys. Generate/export offline; never put the primary secret in GitHub. |
| `APT_SIGNING_PASSPHRASE` | Exact passphrase protecting that export; do not include a trailing newline. |
| `APT_SOURCE_APP_PRIVATE_KEY` | PEM private key of the `cercano-apt-publisher` GitHub App, used only to mint the cross-repository Actions-read token described below. |

`GITHUB_TOKEN` is automatic, not an administrator-created secret. It is used only
against `cercano-ai/packages`. Never supply signing secrets at repository scope.
Rotate/revoke on suspected disclosure; existing public signatures remain verifiable.
Test exports with disposable keys before exporting a production subkey. A full
`--export-secret-subkeys` export may include multiple subkeys; select the exact
subkey (`FINGERPRINT!`) and inspect the export offline.

### Public variables

| Scope | Variable | Value |
| --- | --- | --- |
| `apt-production` | `APT_PRIMARY_FINGERPRINT` | Full 40-hex offline primary fingerprint. |
| `apt-production` | `APT_SIGNING_SUBKEY_FINGERPRINT` | Full 40-hex signing subkey fingerprint (different from primary). |
| `apt-production` | `APT_SOURCE_APP_CLIENT_ID` | Client ID of the `cercano-apt-publisher` GitHub App. |
| Repository | `APT_TOOLCHAIN_IMAGE` | Public `ghcr.io/cercano-ai/apt-toolchain@sha256:<64 lowercase hex>` reviewed immutable OCI digest. Tags are rejected. |
| Repository | `APT_REAL_PACKAGE_ACCEPTED` | Leave absent until the acceptance gate below is independently reviewed; then set exactly `reviewed`. |

Build the optional recipe `toolchain/Dockerfile` on a separate trusted machine for
`linux/amd64`, inspect `/toolchain-packages.txt`, scan the resulting image and run
all tests inside it. Publish to a public GHCR image **only with separate approval**;
record the OCI digest and test that exact digest, not a tag. The recipe pins its base
by digest and package universe by Debian snapshot timestamp, using signed metadata.
There are no runtime dependency installs. An already reviewed equivalent image is
acceptable if it includes bash, Python 3.11+, GnuPG, apt-ftparchive, dpkg-deb, git,
GNU tar/gzip and CA certificates. The image digest pins all executable package
bytes. Actions (including the nested upload-pages artifact action) are SHA-pinned.
GitHub's hosted runner, container launcher and action Node runtime remain managed
platform infrastructure, not bit-for-bit pinned by this repository. Never claim a
fully hermetic build. Review/update immutable pins deliberately, including security
fixes; a frozen snapshot is not automatically secure forever.

### Toolchain base/snapshot compatibility

Treat the base digest and both snapshot timestamps as one reviewed dependency pin.
The current base's `/etc/apt/sources.list.d/debian.sources` embeds
`20261104T212339Z` for Debian and Debian Security. Those immutable snapshots provide
`apt-utils 2.6.2` matching the base's `apt 2.6.2`, and
`perl 5.36.0-7+deb12u4` matching its `perl-base 5.36.0-7+deb12u4`.
The previous January 2025 snapshots supplied `apt-utils 2.6.1` and
`perl 5.36.0-7+deb12u1`, whose exact-version dependencies could not satisfy this
base. Keep the digest fixed when correcting this mismatch; do not force downgrades,
disable signature/dependency checks, or substitute a mutable base tag.
`check-valid-until=no` permits historical snapshot metadata, not unsigned metadata.

For each pin update, inspect the **amd64** base's sources and installed versions,
then run `apt-get update`, `apt-cache policy apt apt-utils perl perl-base`, and
`apt-get --simulate install -y --no-install-recommends python3 gnupg apt-utils ca-certificates git tar gzip`
against only the proposed snapshot URLs in a disposable base container. Require
signed metadata and successful resolution without downgrades/removals. Confirm
with a full `linux/amd64` image build and the portable tests inside that image;
retain the platform, image ID, package inventory and logs. A timestamp comment in
the base is a starting point, not proof that packages remain available.
`tests/test_toolchain.py` locks this verified pair against accidental one-sided
changes; it is an offline regression, **not** a substitute for the build gate.

For the isolated local Colima runtime, explicitly select its context on every
Docker operation (never change the default context):

```sh
docker --context colima-cercano-apt build --platform linux/amd64 --iidfile /tmp/apt-toolchain.iid -t cercano-apt-toolchain:local-amd64 toolchain
docker --context colima-cercano-apt run --rm --platform linux/amd64 --network none -v "$PWD:/src:ro" -w /src "$(cat /tmp/apt-toolchain.iid)" sh -ec 'python3 -B -m unittest discover -s tests -v; PYTHONPYCACHEPREFIX=/tmp/pycache python3 -m py_compile scripts/apt.py tests/test_apt.py tests/test_toolchain.py'
```

### Cross-repository read credential — not the packages GITHUB_TOKEN

A repository's `GITHUB_TOKEN` is scoped to that repository; adding `actions: read`
here does **not** authorize reads of Cercano artifacts. Public repository metadata
can often be queried anonymously, but downloading an Actions artifact requires an
authenticated credential with access.

The credential is a GitHub App installation token. The `prepare` job, after
`apt-production` approval, mints it with a SHA-pinned
`actions/create-github-app-token` step, requesting owner `cercano-ai`, repository
**Cercano only** and **Actions: read**. The token expires after one hour, is passed
to `apt.py` as `SOURCE_ACTIONS_READ_TOKEN` and is revoked when the job ends. The App
(`cercano-apt-publisher`) is owned by `cercano-ai`, installable only on that account,
installed on **Cercano only**, with repository permissions **Actions: read** and
Metadata read, no webhook and no events.

A service-account fine-grained PAT was not chosen: the account would have to be an
organization member, and members inherit the organization's default repository
permission, including read access to private repositories, through a password-based
login that must be protected indefinitely. The App has no login, no organization
membership and no access beyond its installation. Its private key does not expire:
keep it only in the `apt-production` secret, never on disk, and on suspected
disclosure delete the key in the App settings (or uninstall the App) and generate a
new one. Do not use an all-repositories classic PAT.

Check that a token minted from the App can read the workflow-run, workflow,
run-artifacts and `GET /repos/cercano-ai/Cercano/actions/artifacts/ID/zip` endpoints
before scheduling production. Download follows the signed storage redirect
**without forwarding the Authorization header**. Never print the token or signed URL. See GitHub REST
[artifact download permissions](https://docs.github.com/en/rest/actions/artifacts#download-an-artifact)
and [GITHUB_TOKEN scope](https://docs.github.com/en/actions/concepts/security/github_token).
If the source does not expose an API artifact digest, stop; no checksum fallback.

### Job permission inventory

- Guard: none. Prepare: packages `contents: read`. The App private key is used only
  in the token-minting step; the minted source-read token and protected signing
  secrets only in the preparation step.
- Retain and finalize: packages `contents: write` only, solely for release creation,
  assets and the final receipt. No long-lived release-write token is needed.
- Deploy: `pages: write`, `id-token: write` only. Actions artifact transfer uses the
  platform's run-scoped artifact service, not a cross-repository GITHUB_TOKEN grant.
- All unspecified permissions are none; no automatic push, PR, schedule or release trigger.

## Tests and real-package acceptance gate (mandatory)

Local portable checks:

```sh
python3 -m unittest discover -s tests -v
python3 -m py_compile scripts/apt.py tests/test_apt.py
actionlint .github/workflows/apt-pages.yml
shellcheck scripts/build-apt-repo.sh
```

An opt-in synthetic Linux signing/APT integration harness is available at
`tests/integration_apt.py`. Run it **only in a disposable container**, using the
exact-image, read-only-source, network-disabled command and recorded evidence in
[tests/SYNTHETIC_APT_VALIDATION.md](tests/SYNTHETIC_APT_VALIDATION.md). It uses only
fresh disposable keys and minimal actual `.deb` files; it does not satisfy the
real-package acceptance gate or authorize setting `APT_REAL_PACKAGE_ACCEPTED`.

These tests cover hostile ZIP/TAR paths and links, duplicates, checksum/inventory
mismatches, expansion limits, immutable package identities, exact subkey selection,
primary-secret rejection, stale state, missing assets/receipts, broken chains and
main/rerun guards. They are not a substitute for real GPG/dpkg/Pages acceptance.

Before setting the acceptance variable, use the reviewed **exact image digest** in
an isolated Linux fixture environment, with disposable password-protected primary
and signing subkey only. Review evidence for all of the following:

1. Real successful upstream run, artifact ID, digest, source SHA and version; inspect
   every package control (three, or five with arm64), dependencies, ownership and
   maintainer scripts. No
   package installation occurs in the signing job. Record upstream workflow's own
   install/smoke checks. If the complete upstream run failed, do not promote its
   otherwise-successful package job.
2. Exercise artifact validation and build/sign the site with the disposable key.
   Verify `gpgv` trust against the pinned public primary and exact signing subkey;
   `apt update` and install all three packages in a disposable Debian and Ubuntu
   client using `signed-by`, with no insecure/trusted=yes overrides. Confirm amd64
   and `all` indices and installed CLI/agent smoke checks. For a set with arm64,
   repeat on an arm64 client (native, not emulated) and confirm the `arm64` index.
3. Reject full-primary secret import, wrong/extra/expired/revoked subkeys, wrong
   passphrase and altered packages. Verify private files/agent cleanup after both
   successful signing and forced helper failure; logs must contain no secrets.
4. Add a newer version, verify every old pool path remains byte-identical; reject a
   same-name or same-version different-byte package even under another filename.
5. In a **separately approved test repository/environment**, adapt the fixed repository
   guard in a reviewed test-only branch and exercise missing/stale base, upload
   failures, failed/cancelled Pages, receipt failure and overlapping dispatches.
   Confirm pending candidates block further promotions. Inspect environment approval,
   no-self-review, main-only and no-bypass behavior. Never claim local mocks verify
   the GitHub deployment transaction. This implementation has not exercised it.

Record acceptance evidence outside transient Actions artifact retention (e.g. a
reviewed administrator record). This change leaves the gate closed. Do not use a
production signing key merely to test tooling.

## Manual first release and subsequent promotions

1. Complete setup and acceptance above with independent reviewers. Inventory existing
   Pages deployments and all `apt-snapshot-*` Releases. `EMPTY` is allowed only when
   there is no prior service/history; if migrating an existing apt site, stop and
   prepare a separately reviewed seed snapshot retaining its entire pool first.
2. Select a reviewed successful upstream **main** run. Record its full commit, exact
   artifact ID/digest and stable `X.Y.Z` version (without `v`). Download/check it in the
   acceptance environment. Artifacts must still be available and not expired.
3. Dispatch **Protected apt Pages promotion** on main. For first release set
   `expected_base=EMPTY`; otherwise use the latest tag with a verified published
   receipt. Review the source and base at the apt-production approval.
4. Review the newly retained candidate and full snapshot before approving github-pages.
   Do not approve if anything has changed out-of-band. Wait for finalize success.
5. Check `published.json`, Pages URL, `InRelease`, public key fingerprint and apt client
   update/install. Record the successful run. Install the public key via a separately
   authenticated channel; the HTTPS download alone does not authenticate its fingerprint.
   Repository line: `deb [signed-by=/etc/apt/keyrings/cercano.gpg] <Pages URL> stable main`.

## Partial failure, reviewed recovery and rollback

**Never click Re-run jobs** (the guard refuses attempts >1). Never delete a candidate
just to make a red run green. There is no automatic recovery/rollback button.

- Failure before release creation: verify no candidate exists and Pages was untouched;
  fix the issue and dispatch a new run with the same expected published base.
- Candidate exists but no receipt: freeze dispatches, inspect the complete run, assets,
  Pages deployment API/UI and the currently served signed metadata. A timeout/cancel
  does not prove Pages was unchanged. Preserve/download all candidates and published
  snapshots and their digests before investigation. An incomplete release is a lock,
  not permission to start again.
- If Pages demonstrably succeeded with the exact retained candidate, a separately
  reviewed administrator recovery may write its correct `published.json` using the
  same candidate digest/run/URL schema, only after checking the deployment and live
  signatures. If state cannot be proved, do **not** create a receipt.
- Otherwise prepare a separately reviewed one-off recovery workflow/tool which holds
  the same global concurrency group, uses both protected environments and the same
  permission split, revalidates **every** snapshot, forms the union of all observed
  pools (published **and candidate**, including any partially served version), rejects
  byte conflicts, generates fresh signed metadata, retains it durably, deploys, and
  writes a success receipt. Reconcile the incomplete chain explicitly with a reviewed
  migration of state/schema; this normal publisher deliberately will not skip a failed
  candidate. This is a maintenance intervention, not an implemented automation feature.
- A rollback is also a **new** signed deployment with that union pool, never restoration
  of an old snapshot that drops newer files. To stop advertising a bad version, the
  recovery tooling must generate a reviewed selected-version index while retaining all
  pool objects. The normal helper indexes every version; it cannot implement version
  exclusion or force clients to downgrade. Prefer a higher-version corrective release
  for ordinary defects. Document client downgrade/pinning steps if true rollback is
  required. Never republish different bytes for an existing package/version.

Retain all release snapshots indefinitely; no garbage collection is implemented.
Monitor GitHub asset limits, repository storage and the 900 MiB site budget (below
Pages' 1 GB limit). Full snapshots trade storage for bounded, auditable recovery.
If the budget is exceeded, stop and design a reviewed hosting/retention migration;
do not prune historical packages. Release deletion, lost signing keys, signing-key
rotation, upstream artifact expiry, snapshot schema migration and automated rollback
are intentionally not silently handled.
