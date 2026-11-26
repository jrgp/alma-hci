import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


spec = importlib.util.spec_from_file_location(
    "release_notes", Path(__file__).resolve().parents[1] / "ci" / "release-notes.py"
)
notes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notes)


class ReleaseNotesTests(unittest.TestCase):
    def test_diff_lists_upgrades_additions_and_removals_only(self):
        previous = {
            ("zfs", "x86_64"): {"0:2.4.3-1.el10"},
            ("removed", "noarch"): {"0:1-1"},
            ("unchanged", "x86_64"): {"0:1-1"},
        }
        current = {
            ("zfs", "x86_64"): {"0:2.4.4-1.el10"},
            ("added", "noarch"): {"0:1-1"},
            ("unchanged", "x86_64"): {"0:1-1"},
        }
        diff = notes.package_diff(previous, current)
        self.assertIn("| zfs.x86_64 | `2.4.3-1.el10` | `2.4.4-1.el10` |", diff)
        self.assertIn("| added.noarch | - | `1-1` |", diff)
        self.assertIn("| removed.noarch | `1-1` | - |", diff)
        self.assertNotIn("unchanged", diff)

    def test_inventory_preserves_epochs_architectures_and_installonly_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "packages.tsv"
            path.write_text(
                "kernel-core\tx86_64\t0:6.12.0-1.el10\n"
                "kernel-core\tx86_64\t0:6.12.0-2.el10\n"
                "libfoo\ti686\t1:2.0-1\n"
                "libfoo\tx86_64\t2:2.0-1\n"
            )
            packages = notes.read_packages(path)
        self.assertEqual(packages[("kernel-core", "x86_64")],
                         {"0:6.12.0-1.el10", "0:6.12.0-2.el10"})
        self.assertEqual(packages[("libfoo", "i686")], {"1:2.0-1"})
        previous = dict(packages)
        previous[("libfoo", "x86_64")] = {"1:2.0-1"}
        self.assertIn("| libfoo.x86_64 | `1:2.0-1` | `2:2.0-1` |",
                      notes.package_diff(previous, packages))

    def test_unchanged_rpms_are_explicit_even_for_a_new_build(self):
        packages = {("zfs", "x86_64"): {"0:2.4.4-1.el10"}}
        metadata = {
            "release_tag": "10-20261120.42.1",
            "image": "ghcr.io/jrgp/alma-hci@sha256:new",
            "revision": "abcdef123456",
            "previous_image": "ghcr.io/jrgp/alma-hci@sha256:old",
            "base_version": "10.3.20261119.0",
            "base_image": "quay.io/almalinuxorg/almalinux-bootc@sha256:base",
        }
        body = notes.release_notes(metadata, packages, packages, "jrgp/alma-hci", "run-url")
        self.assertIn("No RPM changes detected.", body)
        self.assertIn("ghcr.io/jrgp/alma-hci@sha256:old", body)
        self.assertIn("ghcr.io/jrgp/alma-hci:10-20261120.42.1", body)
        self.assertIn("| Upstream Alma bootc version | `10.3.20261119.0` |", body)
        self.assertIn(
            "| Upstream Alma bootc image | "
            "`quay.io/almalinuxorg/almalinux-bootc@sha256:base` |", body
        )
        metadata["previous_image"] = None
        body = notes.release_notes(metadata, {}, packages, "jrgp/alma-hci", "run-url")
        self.assertIn("First publication", body)
        self.assertIn("| zfs.x86_64 | - | `2.4.4-1.el10` |", body)

    def test_bad_inventory_is_not_reported_as_no_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "packages.tsv"
            for content in ("", "zfs\tx86_64\tmissing-epoch\n"):
                with self.subTest(content=content):
                    path.write_text(content)
                    with self.assertRaises(ValueError):
                        notes.read_packages(path)

    def check_publish_base(self, version="10.3.20261125.0"):
        root = Path(__file__).resolve().parents[1]
        digest = "sha256:" + "1" * 64
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "cosign.pub").write_text("test public key\n")
            for name, content in {
                "cosign": "#!/bin/sh\ncat cosign.pub\n",
                "sudo": """#!/bin/sh
case "$*" in
    *image.base.digest*) printf '%s\\n' "$TEST_BASE_DIGEST" ;;
    *image.version*) printf '20261126.42.1\\n' ;;
    *) echo 'Stopped after reading base metadata' >&2; exit 99 ;;
esac
""",
                "skopeo": """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
Path("inspected-reference").write_text(sys.argv[-1])
if ":10@" in sys.argv[-1]:
    sys.exit("Skopeo does not support tag and digest together")
print(json.dumps({"Labels": {"org.opencontainers.image.version": os.environ["TEST_BASE_VERSION"]}}))
""",
            }.items():
                command = path / name
                command.write_text(content)
                command.chmod(0o755)
            env = dict(os.environ)
            env.update({
                "PATH": f"{path}:{env['PATH']}",
                "BASE_IMAGE": f"quay.io/almalinuxorg/almalinux-bootc:10@{digest}",
                "TEST_BASE_DIGEST": digest,
                "TEST_BASE_VERSION": version,
                **{name: "test" for name in (
                    "IMAGE_REF", "LOCAL_IMAGE", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT",
                    "COSIGN_PRIVATE_KEY", "REGISTRY_AUTH_FILE", "GITHUB_SHA",
                    "GITHUB_REPOSITORY", "GH_TOKEN",
                )},
            })
            result = subprocess.run(
                ["bash", str(root / "ci" / "publish.sh")], cwd=path, env=env,
                capture_output=True, text=True,
            )
            return result, (path / "inspected-reference").read_text(), digest

    def test_publish_inspects_exact_base_digest_without_its_tag(self):
        result, reference, digest = self.check_publish_base()
        self.assertEqual(result.returncode, 99, result.stderr)
        self.assertIn("Stopped after reading base metadata", result.stderr)
        self.assertEqual(reference, f"docker://quay.io/almalinuxorg/almalinux-bootc@{digest}")

    def test_publish_rejects_an_upstream_image_without_a_version(self):
        result, _, _ = self.check_publish_base(version="")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("The upstream Alma bootc image has no version label", result.stderr)
        self.assertNotIn("Stopped after reading base metadata", result.stderr)


if __name__ == "__main__":
    unittest.main()
