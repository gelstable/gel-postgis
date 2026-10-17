import importlib.util
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

    def test_extension_identities_match_planner(self):
        for identity, version in [
            ("3.5.2", "3.5.2"),
            ("3.6.0-RC1", "3.6.0~RC1"),
            ("3.6.0-rc.1+abc", "3.6.0~rc.1"),
        ]:
            with self.subTest(identity=identity):
                self.assertEqual(self.native.parse_identity(identity), ("3", version))

    def test_dependency_distinguishes_portable_builds(self):
        self.assertNotEqual(
            self.native.server_build_dependency("7.1+abc"),
            self.native.server_build_dependency("7.1+def"),
        )

    @unittest.skipUnless(
        all(shutil.which(t) for t in ["nfpm", "dpkg-deb", "rpm"]),
        "Linux package tools required",
    )
    def test_package_headers_payload_and_exact_build_dependency(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            tree = directory / "gel-server-7.1+ec15259"
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
                    self.assertIn(
                        "Depends: gel-server-7, gel-server-7-build-660f42ba83421eb42836a46d4906dce5d4d1855315cb4b8a6cd845fc2e43cb08",
                        fields,
                    )
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
                        [
                            "gel-server-7",
                            "gel-server-7-build-660f42ba83421eb42836a46d4906dce5d4d1855315cb4b8a6cd845fc2e43cb08",
                        ],
                    )

    @unittest.skipUnless(
        all(shutil.which(t) for t in ["nfpm", "dpkg", "rpm"]),
        "Linux package tools required",
    )
    def test_package_managers_reject_different_server_build(self):
        capability = "gel-server-7-build-660f42ba83421eb42836a46d4906dce5d4d1855315cb4b8a6cd845fc2e43cb08"
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            tree = directory / "gel-server-7.1+ec15259"
            tree.mkdir()
            (tree / "extension").write_text("payload")
            extension_config, _ = self.native.render_config(
                tree, ["extension"], "7", "3.5.2", "x86_64", "1", directory / "config"
            )
            for format_ in ["deb", "rpm"]:
                extension = directory / f"extension.{format_}"
                subprocess.run(
                    [
                        "nfpm",
                        "package",
                        "-f",
                        str(extension_config),
                        "-p",
                        format_,
                        "-t",
                        str(extension),
                    ],
                    check=True,
                    capture_output=True,
                )
                for matches in [False, True]:
                    with self.subTest(format=format_, matches=matches):
                        root = directory / f"root-{format_}-{matches}"
                        root.mkdir()
                        config = directory / "server.yaml"
                        config.write_text(
                            "name: gel-server-7\narch: amd64\nplatform: linux\nversion: 7.1\n"
                            "maintainer: Test <test@example.com>\ndescription: Test server\n"
                            "provides:\n  - "
                            + (capability if matches else "gel-server-7-build-other")
                            + "\n"
                        )
                        server = directory / f"server.{format_}"
                        subprocess.run(
                            [
                                "nfpm",
                                "package",
                                "-f",
                                str(config),
                                "-p",
                                format_,
                                "-t",
                                str(server),
                            ],
                            check=True,
                            capture_output=True,
                        )
                        if format_ == "deb":
                            (root / "var/lib/dpkg").mkdir(parents=True)
                            (root / "var/lib/dpkg/status").touch()
                            command = [
                                "dpkg",
                                "--root",
                                str(root),
                                "--force-not-root",
                                "--force-bad-path",
                                "--force-architecture",
                                "--install",
                            ]
                        else:
                            subprocess.run(
                                ["rpm", "--root", str(root), "--initdb"],
                                check=True,
                                capture_output=True,
                            )
                            command = [
                                "rpm",
                                "--root",
                                str(root),
                                "--ignorearch",
                                "--install",
                            ]
                        subprocess.run(
                            command + [str(server)], check=True, capture_output=True
                        )
                        result = subprocess.run(
                            command + [str(extension)], capture_output=True, text=True
                        )
                        self.assertEqual(
                            result.returncode == 0,
                            matches,
                            result.stdout + result.stderr,
                        )
                        if not matches:
                            self.assertIn(capability, result.stdout + result.stderr)

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
