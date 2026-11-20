#!/usr/bin/env python3
"""Install the local image into a sparse file; test two real KVM boots."""

import fcntl
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "vm"
IMAGE = os.environ.get("IMAGE_NAME", "localhost/alma-hci:10")
INSTALL_TARGET = os.environ.get("INSTALL_TARGET_IMAGE")
BOOT_TIMEOUT = int(os.environ.get("BOOT_TIMEOUT", "300"))


def run(args, **kwargs):
    print("+", " ".join(str(arg) for arg in args), flush=True)
    return subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def wait_ssh(ssh, qemu):
    deadline = time.monotonic() + BOOT_TIMEOUT
    while time.monotonic() < deadline:
        if qemu.poll() is not None:
            raise RuntimeError("QEMU exited; inspect output/vm/serial.log")
        result = subprocess.run(
            ssh + ["true"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        if result.returncode == 0:
            return
        time.sleep(2)
    raise TimeoutError("Guest SSH did not become ready; inspect output/vm/serial.log")


def main():
    if os.geteuid() != 0:
        raise RuntimeError("Run with sudo (the installer uses rootful Podman)")
    for program in ("podman", "qemu-system-x86_64", "qemu-img", "ssh", "ssh-keygen"):
        if shutil.which(program) is None:
            raise RuntimeError(f"Missing dependency: {program}")
    if not os.access("/dev/kvm", os.R_OK | os.W_OK):
        raise RuntimeError("/dev/kvm is required; software emulation is not a KVM test")

    os.umask(0o077)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with (OUTPUT / "test.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        test()


def test():
    (OUTPUT / "result.json").unlink(missing_ok=True)
    image = json.loads(
        subprocess.check_output(["podman", "image", "inspect", IMAGE], text=True)
    )[0]
    disk = OUTPUT / "disk.raw"
    data_disk = OUTPUT / "zfs.raw"
    key = OUTPUT / "id_ed25519"
    firmware = next(
        (
            Path(path)
            for path in (
                "/usr/share/edk2/ovmf/OVMF_CODE.fd",
                "/usr/share/OVMF/OVMF_CODE.fd",
                "/usr/share/OVMF/OVMF_CODE_4M.fd",
            )
            if Path(path).exists()
        ),
        None,
    )
    if firmware is None:
        raise RuntimeError("Install OVMF UEFI firmware (edk2-ovmf or ovmf)")
    variables = firmware.with_name(firmware.name.replace("CODE", "VARS"))
    if not variables.exists():
        raise RuntimeError(f"Missing matching UEFI variables template: {variables}")
    shutil.copyfile(variables, OUTPUT / "OVMF_VARS.fd")
    if not key.exists():
        run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", key])
    for path, size in ((disk, "12G"), (data_disk, "1G")):
        path.unlink(missing_ok=True)
        run(["qemu-img", "create", "-f", "raw", path, size])

    # --via-loopback accepts only our file, never a physical host disk.
    target_options = []
    if INSTALL_TARGET:
        target_options = [
            f"--target-imgref={INSTALL_TARGET}", "--enforce-container-sigpolicy",
        ]
    with (OUTPUT / "install.log").open("w") as log:
        run(
            [
                "podman", "run", "--rm", "--privileged", "--pid=host",
                "--security-opt=label=disable",
                "-v", "/var/lib/containers/storage:/var/lib/containers/storage",
                "-v", "/dev:/dev",
                "-v", f"{ROOT / 'output'}:/output",
                IMAGE, "bootc", "install", "to-disk", "--via-loopback",
                "--generic-image", "--filesystem=xfs",
                "--karg=console=ttyS0,115200n8",
                "--root-ssh-authorized-keys=/output/vm/id_ed25519.pub",
                "--skip-fetch-check", *target_options, "/output/vm/disk.raw",
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
        )

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    ssh = [
        "ssh", "-i", str(key), "-p", str(port),
        "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
        "-o", f"UserKnownHostsFile={OUTPUT / 'known_hosts'}",
        "-o", "ConnectTimeout=3", "-o", "ConnectionAttempts=1",
        "root@127.0.0.1",
    ]
    (OUTPUT / "known_hosts").unlink(missing_ok=True)
    command = [
        "qemu-system-x86_64", "-machine", "q35,accel=kvm", "-cpu", "host",
        "-smp", "2", "-m", "3072", "-display", "none", "-monitor", "none",
        "-drive", f"if=pflash,format=raw,readonly=on,file={firmware}",
        "-drive", f"if=pflash,format=raw,file={OUTPUT / 'OVMF_VARS.fd'}",
        "-drive", f"if=none,id=os-disk,format=raw,file={disk}",
        "-device", "virtio-blk-pci,drive=os-disk,serial=alma-os-test,bootindex=1",
        "-drive", f"if=none,id=zfs-test,format=raw,file={data_disk}",
        "-device", "virtio-blk-pci,drive=zfs-test,serial=alma-zfs-test",
        "-netdev", f"user,id=net0,hostfwd=tcp:127.0.0.1:{port}-:22",
        "-device", "virtio-net-pci,netdev=net0",
        "-serial", f"file:{OUTPUT / 'serial.log'}",
    ]
    with (OUTPUT / "qemu.log").open("w") as qemu_log:
        qemu = subprocess.Popen(command, stdout=qemu_log, stderr=subprocess.STDOUT)
        try:
            wait_ssh(ssh, qemu)
            status = json.loads(
                subprocess.check_output(ssh + ["bootc status --json"], text=True)
            )
            if status["status"]["booted"]["image"]["imageDigest"] != image["Digest"]:
                raise RuntimeError("The booted image does not match the local image digest")
            with (OUTPUT / "checks.log").open("w") as log:
                with (ROOT / "tests" / "guest-check.sh").open() as script:
                    run(ssh + ["bash -s -- first"], stdin=script, stdout=log,
                        stderr=subprocess.STDOUT, timeout=180)
                old_boot = subprocess.check_output(
                    ssh + ["cat /proc/sys/kernel/random/boot_id"], text=True
                ).strip()
                run(ssh + ["systemctl reboot"], stdout=log, stderr=subprocess.STDOUT)
                deadline = time.monotonic() + BOOT_TIMEOUT
                while time.monotonic() < deadline:
                    time.sleep(3)
                    wait_ssh(ssh, qemu)
                    new_boot = subprocess.check_output(
                        ssh + ["cat /proc/sys/kernel/random/boot_id"], text=True
                    ).strip()
                    if new_boot != old_boot:
                        break
                else:
                    raise TimeoutError("Guest never rebooted")
                with (ROOT / "tests" / "guest-check.sh").open() as script:
                    run(ssh + ["bash -s -- reboot"], stdin=script, stdout=log,
                        stderr=subprocess.STDOUT, timeout=180)
                run(ssh + ["systemctl poweroff"], stdout=log, stderr=subprocess.STDOUT)
                qemu.wait(timeout=60)
                if qemu.returncode != 0:
                    raise RuntimeError(f"QEMU exited with {qemu.returncode}")
            result = {
                "image": IMAGE,
                "image_id": image["Id"],
                "image_digest": image["Digest"],
                "result": "PASS",
                "checks": [
                    "UEFI boot and reboot", "bootc deployment and immutable /usr",
                    "SELinux enforcing", "ZFS automatic module loading",
                    "ZFS pool IO, snapshot, clone, export/import, scrub",
                    "ZFS automatic import and data persistence after reboot",
                    "libvirt default NAT network", "nested KVM domain via libvirt",
                ],
            }
            (OUTPUT / "result.json").write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps(result, indent=2), flush=True)
        finally:
            if qemu.poll() is None:
                qemu.terminate()
                try:
                    qemu.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    qemu.kill()
                    qemu.wait()


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"FAIL: {error}\nLogs: {OUTPUT}", file=sys.stderr)
        sys.exit(1)
