import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class UpdateCheckTests(unittest.TestCase):
    def check(self, rpm_status=0, changed_source=False, changed_base=False):
        tree = subprocess.check_output(
            ["git", "ls-tree", "-r", "HEAD", "--", "Dockerfile", ".containerignore",
             "files", "ci", "tests", "cosign.pub", "bootc.pub",
             ".github/workflows/build.yml"], cwd=ROOT,
        )
        source_hash = hashlib.sha256(tree).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            for name, content in {
                "cosign": "#!/bin/sh\nexit 0\n",
                "skopeo": """#!/usr/bin/env python3
import json, os, sys
if "--format" in sys.argv:
    print("sha256:current")
else:
    print(os.environ["PREVIOUS_IMAGE"])
""",
                "sudo": """#!/bin/sh
shift
printf 'RPM check ran\\n' >&2
exit "$RPM_STATUS"
""",
            }.items():
                command = path / name
                command.write_text(content)
                command.chmod(0o755)
            env = dict(os.environ)
            env.update({
                "PATH": f"{path}:{env['PATH']}",
                "FORCE": "false",
                "GITHUB_OUTPUT": str(path / "outputs"),
                "GITHUB_STEP_SUMMARY": str(path / "summary"),
                "RPM_STATUS": str(rpm_status),
                "PREVIOUS_IMAGE": json.dumps({
                    "Digest": "sha256:published",
                    "Labels": {
                        "org.opencontainers.image.base.digest":
                            "sha256:old" if changed_base else "sha256:current",
                        "io.github.alma-hci.source-hash":
                            "old" if changed_source else source_hash,
                    },
                }),
            })
            result = subprocess.run(
                ["bash", "ci/check-updates.sh"], cwd=ROOT, env=env,
                capture_output=True, text=True,
            )
            outputs = (path / "outputs").read_text() if (path / "outputs").exists() else ""
            return result, outputs

    def test_unchanged_skips_build(self):
        result, outputs = self.check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("build=false\n", outputs)
        self.assertIn("RPM check ran", result.stderr)

    def test_package_updates_trigger_build(self):
        result, outputs = self.check(rpm_status=100)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("build=true\n", outputs)

    def test_source_or_base_changes_avoid_rpm_probe(self):
        for change in ("changed_source", "changed_base"):
            with self.subTest(change=change):
                result, outputs = self.check(**{change: True})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("build=true\n", outputs)
                self.assertNotIn("RPM check ran", result.stderr)

    def test_repository_failure_is_not_no_changes(self):
        result, outputs = self.check(rpm_status=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing to report no changes", result.stderr)
        self.assertNotIn("build=false", outputs)


if __name__ == "__main__":
    unittest.main()
