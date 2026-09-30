# Native PostGIS packages

Build on a Linux host matching the requested architecture with Python 3.12+,
zstd, and nFPM 2.47.0. The builder verifies each published portable extension
ZIP and server tarball against the GitHub API SHA256 and byte count before use.

```sh
python3 scripts/native_packages.py --slot 7 --server-source-tag v7.1 \
  --source-tag legacy-gel-server-7-ext-postgis --arch aarch64 \
  --aarch64-asset gel-server-7-ext-postgis-3.5.1+edaa7ce-aarch64-unknown-linux-gnu.zip
```

The supported `gel-load-ext` installer runs against a scratch portable server.
All existing paths must retain their type, mode, SHA256 or symlink target. Only
added files and symlinks are included under `/usr/lib/gel-server-<slot>/`.
The packages preserve executable modes and symlinks and depend only on
`gel-server-<slot>`, without a version constraint or catalog dependency.
Epoch 1 makes native versions newer than legacy versions with build hashes.
The full portable extension identity remains in the description.

The native packaging workflow accepts exact ZIP names for each architecture,
a portable server source tag, and a native `server_package_tag` for container
acceptance. Both server tags must target the requested compatible slot. The
native server release must already exist before this workflow is used; its test
packages are also fetched with API SHA256/size verification. Tests install on
Debian 12 and Rocky Linux 9, apply a schema with `using extension postgis`, and
query a geometry function while checking the server package version is unchanged.

The legacy Bookworm amd64 index at
`https://packages.geldata.com/apt/dists/bookworm/main/binary-amd64/Packages`
contains `gel-server-7-ext-postgis` and `gel-server-7-rc3-ext-postgis`. Their
presence in the repository does not establish that they were installed together.
Neither entry declares replacement/conflict relationships. No prerelease name
replacement is therefore declared by these packages.

Build jobs use no secrets. Signing is restricted to the default `main` branch
and the protected `package-signing` Environment. Configure that Environment
for the default branch with `PACKAGE_SIGNING_KEY` and `PACKAGE_SIGNING_FPR`.
RPM verification uses an isolated database containing only the public key.
The release job computes the v2 manifest from final signed bytes, validates its
vendored registry schema, uploads a draft, checks API digest/size for packages
and manifest, then publishes. Testing releases are prereleases. Existing releases
and assets are never replaced; retries after a retained failed draft need a new
revision. Production publication requires the Environment protection configured
in GitHub.
