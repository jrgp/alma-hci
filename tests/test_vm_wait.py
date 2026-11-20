import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location(
    "vm_harness", Path(__file__).with_name("test-vm.py")
)
vm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vm)


class BootWaitTests(unittest.TestCase):
    def test_reboot_retries_disconnects_and_requires_new_boot_id(self):
        responses = [
            subprocess.CompletedProcess([], 0, stdout="old\n"),
            subprocess.CompletedProcess([], 255, stdout=""),
            subprocess.CompletedProcess([], 0, stdout="old\n"),
            subprocess.CompletedProcess([], 0, stdout="new\n"),
        ]
        with patch.object(vm.subprocess, "run", side_effect=responses) as run, \
                patch.object(vm.time, "sleep"):
            vm.wait_ssh(["ssh", "guest"], Mock(poll=Mock(return_value=None)),
                        previous_boot="old")
        self.assertEqual(run.call_count, 4)
        self.assertTrue(all(
            call.args[0] == ["ssh", "guest", "cat /proc/sys/kernel/random/boot_id"]
            for call in run.call_args_list
        ))

    def test_missing_reboot_times_out(self):
        with patch.object(vm.time, "monotonic", side_effect=[0, vm.BOOT_TIMEOUT]):
            with self.assertRaisesRegex(TimeoutError, "never rebooted"):
                vm.wait_ssh(["ssh", "guest"], Mock(), previous_boot="old")

    def test_qemu_exit_fails_without_waiting(self):
        with self.assertRaisesRegex(RuntimeError, "QEMU exited"):
            vm.wait_ssh(["ssh", "guest"], Mock(poll=Mock(return_value=1)))


if __name__ == "__main__":
    unittest.main()
