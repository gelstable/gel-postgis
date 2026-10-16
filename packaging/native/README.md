# Native PostGIS packages

Build on a Linux host matching the requested architecture with Python 3.12+,
zstd, and nFPM 2.47.0. The shared `native-plan` action selects portable sources,
resolves legacy extension builds through the portable registry, and allocates
package revisions. Source downloads are verified against their SHA256 and byte
count before unpacking.

The workflow supports three entry points:

- Dispatch with `source_tag` and optional `repack`. An already packaged release
  succeeds without work unless `repack` requests the next revision.
- Call it with `portable-artifact` and optional `revision` (default `1`). The
  artifact must contain exactly one glibc extension ZIP and matching portable
  server tarball for each architecture, preserving their original basenames.
  For example, `gel-server-7-ext-postgis-3.5.1+c9b5460-x86_64-unknown-linux-gnu.zip`
  and `gel-server-x86_64-unknown-linux-gnu.tar.zst`. The caller provides trusted
  build artifacts; no public release is needed. Signed packages are returned as
  the `native-packages` Actions artifact for the caller's release and manifest.
- Pull requests build `legacy-gel-server-7-ext-postgis` on both architectures
  without signing or publishing.

To build a downloaded Actions artifact locally:

```sh
python3 scripts/native_packages.py --portable-directory dist/portable \
  --revision 1 --arch aarch64
```

The workflow downloads the plan’s selected sources with `curl`, verifies their
SHA256 with `sha256sum`, and checks their byte count. The builder only reads
local archives; it does not access the network.

The supported `gel-load-ext` installer runs against a scratch portable server.
All existing paths must retain their type, mode, SHA256 or symlink target. Only
added files and symlinks are included under `/usr/lib/gel-server-<slot>/`.
The packages preserve executable modes and symlinks and depend only on
`gel-server-<slot>`, without a version constraint or catalog dependency.
Epoch 1 makes native versions newer than legacy versions with build hashes.
The full portable extension identity remains in the description.

Container tests install `gel-server-<slot>` and `gel-cli` from the production
registry's stable channel after checking its pinned primary key fingerprint.
They install the built extension on Debian 12 and Rocky Linux 9, apply a schema
with `using extension postgis`, query a geometry function, and check that the
server package version remains unchanged. Before server packages are published,
production install tests cannot pass. For local acceptance with a signed fixture
registry, set `NATIVE_REGISTRY_URL` and `NATIVE_REGISTRY_FINGERPRINT` in the
container; the registry must serve the key and the same source-file paths as
production, with URLs pointing to the fixture. Signature verification remains
enabled. Built extension RPMs may be unsigned in these build-only tests.

The legacy Bookworm amd64 index contains `gel-server-7-ext-postgis` and
`gel-server-7-rc3-ext-postgis`. Their presence does not establish that they were
installed together. Neither declares replacement/conflict relationships, so no
prerelease name replacement is declared.

Build jobs use no signing secrets. Signing runs only on the default branch or
`release/*` branches in the protected `package-signing` Environment, configured
with `PACKAGE_SIGNING_KEY` and `PACKAGE_SIGNING_FPR`. Configure that Environment
to permit only those branches. Reusable callers must run on a branch permitted
by that Environment; the sign job obtains its secrets from the Environment.
The caller’s `uses` job must grant `permissions: {contents: write}` because
GitHub validates the nested dispatch-only release job’s permission envelope
even when that job is skipped for a reusable call. The caller must also forward
both required signing secret names, even when their values exist only in the
protected Environment. GitHub resolves those Environment values in the sign job:

```yaml
jobs:
  packages:
    permissions:
      contents: write
    uses: ./.github/workflows/native-packages.yml
    with:
      portable-artifact: portable-postgis
    secrets:
      PACKAGE_SIGNING_KEY: ${{ secrets.PACKAGE_SIGNING_KEY }}
      PACKAGE_SIGNING_FPR: ${{ secrets.PACKAGE_SIGNING_FPR }}
```

Missing secret forwarding fails reusable-call validation before builds start.
Shared actions sign and independently verify
RPMs, generate and validate the final manifest, and publish a digest-verified
native-only release for dispatch runs. Failed drafts are cleaned up; existing
published releases and assets are immutable. Testing versions publish as
prereleases. Reusable calls return packages without publishing a release.
