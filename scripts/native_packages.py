#!/usr/bin/env python3
"""Build native PostGIS packages from digest-verified portable releases.

Requires Python 3.12+, zstd and nFPM. No server compilation is performed.
"""

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHES = {"x86_64": ("amd64", "x86_64"), "aarch64": ("arm64", "aarch64")}


def parse_identity(identity):
    match = re.fullmatch(
        r"([1-9][0-9]*)(\.[0-9]+(?:\.[0-9]+)?)(-[A-Za-z0-9.]+)?"
        r"(?:\+([A-Za-z0-9._]+))?",
        identity,
    )
    if not match:
        raise ValueError(f"unsupported portable build identity: {identity}")
    version = identity.split("+", 1)[0].replace("-", "~", 1)
    return match[1], version


def server_build_dependency(identity):
    """A package capability tied to the complete portable build, across repacks."""
    slot, _ = parse_identity(identity)
    digest = hashlib.sha256(identity.encode("ascii")).hexdigest()
    return f"gel-server-{slot}-build-{digest}"


def extract_portable(archive, directory):
    """Unpack a verified zstd tar using Python's path/link safety filter."""
    with tempfile.TemporaryFile() as uncompressed:
        subprocess.run(
            ["zstd", "-d", "-c", str(archive)], stdout=uncompressed, check=True
        )
        uncompressed.seek(0)
        with tarfile.open(fileobj=uncompressed) as source:
            members = source.getmembers()
            members = [m for m in members if m.name.rstrip("/") not in {".", ""}]
            top = {member.name.removeprefix("./").split("/")[0] for member in members}
            if len(top) != 1 or not next(iter(top)).startswith("gel-server-"):
                raise ValueError("expected exactly one portable server tree")
            identity = next(iter(top)).removeprefix("gel-server-")
            parse_identity(identity)
            source.extractall(directory, filter="data")
    tree = Path(directory) / ("gel-server-" + identity)
    if not (tree / "bin/gel-server").is_file():
        raise ValueError("portable server executable missing")
    return tree


def snapshot(tree):
    result = {}
    for path in Path(tree).rglob("*"):
        mode = path.lstat().st_mode
        if path.is_symlink():
            value = ("link", os.readlink(path))
        elif path.is_file():
            with path.open("rb") as file:
                value = ("file", hashlib.file_digest(file, "sha256").hexdigest())
        elif path.is_dir():
            value = ("directory",)
        else:
            raise ValueError("unsupported portable file type")
        result[path.relative_to(tree).as_posix()] = (mode, value)
    return result


def install_extension(tree, archive):
    """Use the supported loader and reject any changes to existing paths."""
    before = snapshot(tree)
    subprocess.run([str(Path(tree) / "bin/gel-load-ext"), str(archive)], check=True)
    after = snapshot(tree)
    for name, value in before.items():
        if after.get(name) != value:
            raise ValueError(f"extension installer modified existing path: {name}")
    additions = sorted(
        name
        for name in after.keys() - before.keys()
        if after[name][1][0] != "directory"
    )
    if not additions:
        raise ValueError("extension installer added no files")
    return additions


def render_config(tree, additions, slot, identity, arch, revision, directory):
    if arch not in ARCHES or not re.fullmatch(r"[1-9][0-9]*", revision):
        raise ValueError("unsupported architecture or package revision")
    if not re.fullmatch(r"[1-9][0-9]*", slot):
        raise ValueError("invalid server slot")
    _, version = parse_identity(identity)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    values = {
        "SLOT": slot,
        "VERSION": version,
        "REVISION": revision,
        "ARCH": ARCHES[arch][0],
        "IDENTITY": identity,
        "SERVER_BUILD": server_build_dependency(
            Path(tree).name.removeprefix("gel-server-")
        ),
    }
    text = (ROOT / "packaging/native/gel-server-ext-postgis.nfpm.yaml.in").read_text()
    for key, value in values.items():
        text = text.replace("@" + key + "@", value)
    for name in additions:
        source = Path(tree) / name
        content = {"src": str(source), "dst": f"/usr/lib/gel-server-{slot}/{name}"}
        if source.is_symlink():
            content.update(src=os.readlink(source), type="symlink")
        else:
            content["file_info"] = {"mode": source.stat().st_mode & 0o7777}
        # JSON scalars are valid YAML; nFPM handles all package formats.
        text += "  - " + json.dumps(content) + "\n"
    config = directory / "postgis.nfpm.yaml"
    config.write_text(text)
    return config, {
        "slot": slot,
        "version": version,
        "revision": revision,
        "identity": identity,
        "arch": arch,
        "tag": f"pkg-gel-server-{slot}-ext-postgis-{version.replace('~', '-')}-{revision}",
    }


def build(extension, server, arch, revision, out):
    if platform.system() != "Linux" or platform.machine() != arch:
        raise ValueError(
            "extension installer requires a matching Linux host architecture"
        )
    match = re.fullmatch(
        rf"gel-server-([1-9][0-9]*)-ext-postgis-(.+)-{re.escape(arch)}-unknown-linux-gnu\.zip",
        Path(extension).name,
    )
    if not match:
        raise ValueError("extension asset must match slot and architecture")
    slot, identity = match.groups()
    _, version = parse_identity(identity)
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        tree = extract_portable(server, temp)
        if parse_identity(tree.name.removeprefix("gel-server-"))[0] != slot:
            raise ValueError("portable server slot mismatch")
        additions = install_extension(tree, extension)
        config, metadata = render_config(
            tree, additions, slot, identity, arch, revision, Path(temp) / "rendered"
        )
        for format_, native_arch in zip(["deb", "rpm"], ARCHES[arch]):
            name = (
                f"gel-server-{slot}-ext-postgis-{version.replace('~', '-')}"
                f"-{revision}-{native_arch}.{format_}"
            )
            subprocess.run(
                [
                    "nfpm",
                    "package",
                    "--config",
                    str(config),
                    "--packager",
                    format_,
                    "--target",
                    str(out / name),
                ],
                check=True,
            )
    metadata["additions"] = additions
    (out / f"build-info-{arch}.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portable-directory", type=Path, required=True)
    parser.add_argument("--revision", default="1")
    parser.add_argument("--arch", choices=ARCHES, required=True)
    parser.add_argument("--out", type=Path, default=Path("dist/native"))
    args = parser.parse_args()
    extensions = list(
        args.portable_directory.rglob(
            f"gel-server-*-ext-postgis-*-{args.arch}-unknown-linux-gnu.zip"
        )
    )
    servers = list(
        args.portable_directory.rglob(
            f"gel-server-{args.arch}-unknown-linux-gnu.tar.zst"
        )
    )
    if len(extensions) != 1 or len(servers) != 1:
        raise ValueError(
            "artifact must contain one extension ZIP and matching server tarball per architecture"
        )
    print(
        json.dumps(build(extensions[0], servers[0], args.arch, args.revision, args.out))
    )


if __name__ == "__main__":
    main()
