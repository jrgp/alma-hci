import importlib.util
from pathlib import Path
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
        }
        body = notes.release_notes(metadata, packages, packages, "jrgp/alma-hci", "run-url")
        self.assertIn("No RPM changes detected.", body)
        self.assertIn("ghcr.io/jrgp/alma-hci@sha256:old", body)
        self.assertIn("ghcr.io/jrgp/alma-hci:10-20261120.42.1", body)
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


if __name__ == "__main__":
    unittest.main()
