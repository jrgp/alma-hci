import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location(
    "upgrade_harness", Path(__file__).with_name("test-vm.py")
)
vm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vm)

OLD = "sha256:" + "1" * 64
NEW = "sha256:" + "2" * 64
REPOSITORY = "ghcr.io/jrgp/alma-hci@"


def status(booted, staged=None, rollback=None):
    def deployment(digest):
        return {"image": {"imageDigest": digest}} if digest else None

    return {"status": {
        "booted": deployment(booted),
        "staged": deployment(staged),
        "rollback": deployment(rollback),
    }}


class UpgradeTests(unittest.TestCase):
    def check_upgrade(self, staged, upgraded):
        with tempfile.TemporaryDirectory() as output, \
                patch.object(vm, "OUTPUT", Path(output)), \
                patch.object(vm, "UPGRADE_FROM", REPOSITORY + OLD), \
                patch.object(vm, "UPGRADE_TO", REPOSITORY + NEW), \
                patch.object(vm, "run") as run, \
                patch.object(vm, "power_transition") as transition, \
                patch.object(vm, "wait_ssh") as wait, \
                patch.object(vm.subprocess, "check_output", side_effect=[
                    json.dumps(status(OLD)), "old-boot\n", json.dumps(staged),
                    "old-boot\n", json.dumps(upgraded),
                ]):
            result = vm.check_upgrade(["ssh", "guest"], Mock(), io.StringIO())
            transition.assert_called_once()
            wait.assert_called_once()
            self.assertEqual(wait.call_args.kwargs["previous_boot"], "old-boot")
            commands = [call.args[0][-1] for call in run.call_args_list]
            self.assertIn(
                f"bootc switch --enforce-container-sigpolicy {REPOSITORY}{NEW}", commands
            )
            self.assertEqual(json.loads(
                (Path(output) / "upgraded-status.json").read_text()
            ), upgraded)
            return result

    def test_exact_candidate_boots_with_previous_deployment_retained(self):
        result = self.check_upgrade(status(OLD, NEW), status(NEW, rollback=OLD))
        self.assertEqual(result["result"], "PASS")
        self.assertEqual(result["image"], REPOSITORY + NEW)

    def test_staging_wrong_image_fails(self):
        with self.assertRaisesRegex(RuntimeError, "Expected staged image"):
            self.check_upgrade(status(OLD, OLD), status(NEW, rollback=OLD))

    def test_fallback_to_old_image_is_not_an_upgrade(self):
        with self.assertRaisesRegex(RuntimeError, "Expected booted image"):
            self.check_upgrade(status(OLD, NEW), status(OLD, NEW))

    def test_candidate_left_staged_after_reboot_fails(self):
        with self.assertRaisesRegex(RuntimeError, "still staged"):
            self.check_upgrade(status(OLD, NEW), status(NEW, NEW, OLD))

    def test_missing_rollback_deployment_fails(self):
        with self.assertRaisesRegex(RuntimeError, "Expected rollback image"):
            self.check_upgrade(status(OLD, NEW), status(NEW))

    def test_requires_immutable_references(self):
        with self.assertRaisesRegex(RuntimeError, "immutable"):
            vm.upgrade_digest("ghcr.io/jrgp/alma-hci:10")


if __name__ == "__main__":
    unittest.main()
