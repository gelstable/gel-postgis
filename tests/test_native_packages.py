import hashlib
import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class NativePackagesTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "native", ROOT / "scripts/native_packages.py"
        )
        self.native = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.native)

    def test_verified_source_rejects_missing_digest_and_wrong_cached_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            source.write_bytes(b"payload")
            output = Path(temp) / "download"
            output.write_bytes(b"corrupt")
            asset = {
                "browser_download_url": source.as_uri(),
                "size": 7,
                "digest": "sha256:" + hashlib.sha256(b"payload").hexdigest(),
            }
            self.native.fetch_asset(asset, output)
            self.assertEqual(output.read_bytes(), b"payload")
            asset["size"] = 8
            with self.assertRaisesRegex(ValueError, "mismatch"):
                self.native.fetch_asset(asset, output)
            del asset["digest"]
            with self.assertRaisesRegex(ValueError, "digest"):
                self.native.fetch_asset(asset, output)

    def test_manifest_uses_final_bytes_and_rejects_duplicate_architecture(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            for arch, names in self.native.ARCHES.items():
                info = {
                    "slot": "7",
                    "version": "3.5.1",
                    "revision": "1",
                    "arch": arch,
                    "channel": "testing",
                    "tag": "pkg-gel-server-7-ext-postgis-3.5.1-1",
                }
                (directory / f"build-info-{arch}.json").write_text(json.dumps(info))
                for format_, name in zip(["deb", "rpm"], names):
                    (
                        directory / f"gel-server-7-ext-postgis-3.5.1-1-{name}.{format_}"
                    ).write_bytes(b"final signed bytes")
            _, manifest = self.native.manifest(directory, "testing")
            self.assertEqual(len(manifest["native"]["packages"]), 4)
            for package in manifest["native"]["packages"]:
                self.assertEqual(
                    package["sha256"], hashlib.sha256(b"final signed bytes").hexdigest()
                )
                self.assertEqual(package["size"], 18)
            info["arch"] = "x86_64"
            (directory / "build-info-aarch64.json").write_text(json.dumps(info))
            with self.assertRaisesRegex(ValueError, "architecture mismatch"):
                self.native.manifest(directory, "testing")

    @unittest.skipUnless(
        all(shutil.which(t) for t in ["nfpm", "dpkg-deb", "rpm"]),
        "Linux package tools required",
    )
    def test_package_headers_payload_and_slot_only_dependency(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            tree = directory / "tree"
            tree.mkdir()
            (tree / "extension").write_text("payload")
            (tree / "extension").chmod(0o640)
            (tree / "alias").symlink_to("extension")
            config, _ = self.native.render_config(
                tree,
                ["alias", "extension"],
                "7",
                "3.5.1+c9b5460",
                "x86_64",
                "2",
                directory / "config",
            )
            for format_ in ["deb", "rpm"]:
                package = directory / f"package.{format_}"
                subprocess.run(
                    [
                        "nfpm",
                        "package",
                        "-f",
                        str(config),
                        "-p",
                        format_,
                        "-t",
                        str(package),
                    ],
                    check=True,
                    capture_output=True,
                )
                if format_ == "deb":
                    fields = subprocess.check_output(
                        [
                            "dpkg-deb",
                            "-f",
                            str(package),
                            "Package",
                            "Version",
                            "Architecture",
                            "Depends",
                        ],
                        text=True,
                    )
                    self.assertIn("Version: 1:3.5.1-2", fields)
                    self.assertIn("Depends: gel-server-7\n", fields)
                    subprocess.run(
                        ["dpkg-deb", "-x", str(package), str(directory / "extracted")],
                        check=True,
                    )
                    payload = directory / "extracted/usr/lib/gel-server-7"
                    self.assertEqual((payload / "extension").read_text(), "payload")
                    self.assertEqual(
                        (payload / "extension").stat().st_mode & 0o777, 0o640
                    )
                    self.assertEqual((payload / "alias").readlink(), Path("extension"))
                else:
                    fields = subprocess.check_output(
                        [
                            "rpm",
                            "-qp",
                            "--qf",
                            "%{NAME} %{EPOCH}:%{VERSION}-%{RELEASE} %{ARCH}",
                            str(package),
                        ],
                        text=True,
                    )
                    self.assertEqual(
                        fields, "gel-server-7-ext-postgis 1:3.5.1-2 x86_64"
                    )
                    dependencies = subprocess.check_output(
                        ["rpm", "-qp", "--requires", str(package)], text=True
                    ).splitlines()
                    self.assertEqual(
                        [d for d in dependencies if not d.startswith("rpmlib(")],
                        ["gel-server-7"],
                    )

    def test_installer_packages_additions_and_rejects_mutations(self):
        spec = importlib.util.spec_from_file_location(
            "native", ROOT / "scripts/native_packages.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temp:
            tree = Path(temp)
            (tree / "bin").mkdir()
            loader = tree / "bin/gel-load-ext"
            loader.write_text(
                '#!/bin/sh\nmkdir -p "$(dirname "$0")/../share"\necho addition > "$(dirname "$0")/../share/postgis"\n'
            )
            loader.chmod(0o755)
            self.assertEqual(
                module.install_extension(tree, tree / "ext.zip"), ["share/postgis"]
            )
            loader.write_text(
                '#!/bin/sh\necho mutation >> "$(dirname "$0")/../share/postgis"\n'
            )
            with self.assertRaisesRegex(ValueError, "modified"):
                module.install_extension(tree, tree / "ext.zip")


if __name__ == "__main__":
    unittest.main()
