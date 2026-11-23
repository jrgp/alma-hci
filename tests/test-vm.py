#!/usr/bin/env python3
"""Test fresh installs or signed image upgrades with Secure Boot and ZFS."""

import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
UPGRADE_FROM = os.environ.get("UPGRADE_FROM_IMAGE")
UPGRADE_TO = os.environ.get("UPGRADE_TO_IMAGE")
OUTPUT = ROOT / "output" / ("vm-upgrade" if UPGRADE_FROM else "vm")
IMAGE = UPGRADE_FROM or os.environ.get("IMAGE_NAME", "localhost/alma-hci:10")
INSTALL_TARGET = UPGRADE_FROM or os.environ.get("INSTALL_TARGET_IMAGE")
BOOT_TIMEOUT = int(os.environ.get("BOOT_TIMEOUT", "300"))


def run(args, **kwargs):
    print("+", " ".join(str(arg) for arg in args), flush=True)
    return subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def wait_ssh(ssh, qemu, previous_boot=None):
    deadline = time.monotonic() + BOOT_TIMEOUT
    while time.monotonic() < deadline:
        if qemu.poll() is not None:
            raise RuntimeError(f"QEMU exited; inspect {OUTPUT / 'serial.log'}")
        result = subprocess.run(
            ssh + ["cat /proc/sys/kernel/random/boot_id"],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and result.stdout.strip() and result.stdout.strip() != previous_boot:
            return
        time.sleep(2)
    if previous_boot is not None:
        raise TimeoutError(f"Guest never rebooted; inspect {OUTPUT / 'serial.log'}")
    raise TimeoutError(f"Guest SSH did not become ready; inspect {OUTPUT / 'serial.log'}")


def power_transition(ssh, action, log):
    # Let SSH acknowledge the request before shutdown tears down the connection.
    run(
        ssh + [f"systemd-run --on-active=2s --timer-property=AccuracySec=1s systemctl {action}"],
        stdout=log, stderr=subprocess.STDOUT,
    )


def assert_deployment(status, slot, digest):
    deployment = status["status"][slot]
    actual = deployment["image"]["imageDigest"] if deployment else None
    if actual != digest:
        raise RuntimeError(f"Expected {slot} image {digest}, got {actual}")


def upgrade_digest(reference):
    if not re.fullmatch(r"[^@\s]+@sha256:[0-9a-f]{64}", reference):
        raise RuntimeError("Upgrade test requires immutable image@sha256 references")
    return reference.rsplit("@", 1)[1]


def check_upgrade(ssh, qemu, log):
    previous_digest = upgrade_digest(UPGRADE_FROM)
    target_digest = upgrade_digest(UPGRADE_TO)
    if previous_digest == target_digest:
        raise RuntimeError("Upgrade baseline and candidate must be different images")

    def status_at(stage):
        status = json.loads(subprocess.check_output(ssh + ["bootc status --json"], text=True))
        (OUTPUT / f"{stage}-status.json").write_text(json.dumps(status, indent=2) + "\n")
        return status

    def guest_check(stage):
        with (ROOT / "tests" / "guest-upgrade.sh").open() as script:
            run(ssh + [f"bash -s -- {stage}"], stdin=script, stdout=log,
                stderr=subprocess.STDOUT, timeout=180)

    assert_deployment(status_at("baseline"), "booted", previous_digest)
    guest_check("baseline")
    old_boot = subprocess.check_output(
        ssh + ["cat /proc/sys/kernel/random/boot_id"], text=True
    ).strip()
    run(ssh + [f"bootc switch --enforce-container-sigpolicy {shlex.quote(UPGRADE_TO)}"],
        stdout=log, stderr=subprocess.STDOUT, timeout=900)
    status = status_at("staged")
    assert_deployment(status, "booted", previous_digest)
    assert_deployment(status, "staged", target_digest)
    if subprocess.check_output(
        ssh + ["cat /proc/sys/kernel/random/boot_id"], text=True
    ).strip() != old_boot:
        raise RuntimeError("Staging unexpectedly rebooted the guest")
    power_transition(ssh, "reboot", log)
    wait_ssh(ssh, qemu, previous_boot=old_boot)
    status = status_at("upgraded")
    assert_deployment(status, "booted", target_digest)
    assert_deployment(status, "rollback", previous_digest)
    if status["status"]["staged"] is not None:
        raise RuntimeError("Candidate is still staged instead of fully booted")
    guest_check("upgraded")
    return {
        "previous_image": UPGRADE_FROM,
        "image": UPGRADE_TO,
        "result": "PASS",
        "checks": [
            "Previous published digest booted with Secure Boot and its enrolled MOK",
            "Signed candidate staged without rebooting; previous digest still running",
            "New boot ID and exact candidate digest after reboot; no fallback",
            "Previous deployment retained for rollback",
            "ZFS automatically loads with the existing MOK and running kernel",
            "ZFS pool auto-import, payload, snapshot, new writes and scrub after upgrade",
            "Persistent /etc and /var data; SELinux enforcing",
        ],
    }


def main():
    if bool(UPGRADE_FROM) != bool(UPGRADE_TO):
        raise RuntimeError("Set both UPGRADE_FROM_IMAGE and UPGRADE_TO_IMAGE")
    if UPGRADE_FROM:
        upgrade_digest(UPGRADE_FROM)
        upgrade_digest(UPGRADE_TO)
    if os.geteuid() != 0:
        raise RuntimeError("Run with sudo (the installer uses rootful Podman)")
    for program in (
        "podman", "qemu-system-x86_64", "qemu-img", "ssh", "ssh-keygen", "virt-fw-vars",
    ):
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
    if UPGRADE_FROM:
        run(["podman", "pull", UPGRADE_FROM])
    image = json.loads(
        subprocess.check_output(["podman", "image", "inspect", IMAGE], text=True)
    )[0]
    disk = OUTPUT / "disk.raw"
    data_disk = OUTPUT / "zfs.raw"
    key = OUTPUT / "id_ed25519"
    firmware_pair = next(
        (
            (Path(code), Path(variables))
            for code, variables in (
                ("/usr/share/edk2/ovmf/OVMF_CODE.secboot.fd",
                 "/usr/share/edk2/ovmf/OVMF_VARS.fd"),
                ("/usr/share/OVMF/OVMF_CODE_4M.secboot.fd",
                 "/usr/share/OVMF/OVMF_VARS_4M.fd"),
                ("/usr/share/OVMF/OVMF_CODE.secboot.fd",
                 "/usr/share/OVMF/OVMF_VARS.fd"),
            )
            if Path(code).exists() and Path(variables).exists()
        ),
        None,
    )
    if firmware_pair is None:
        raise RuntimeError("Install Secure Boot OVMF firmware (edk2-ovmf or ovmf)")
    firmware, variables = firmware_pair
    nvram = OUTPUT / "OVMF_VARS.fd"
    run([
        "virt-fw-vars", "--input", variables, "--output", nvram,
        "--enroll-redhat", "--sb",
    ])
    cert = OUTPUT / "zfs.der"
    with cert.open("wb") as stream:
        run(["podman", "run", "--rm", IMAGE, "cat", "/usr/share/alma-hci/zfs.der"],
            stdout=stream)
    if UPGRADE_FROM:
        enrolled = OUTPUT / "OVMF_VARS.enrolled.fd"
        run([
            "virt-fw-vars", "--input", nvram, "--output", enrolled,
            "--add-mok", "605dab50-e046-4300-abb6-3dd810dd8b23", cert,
        ])
        enrolled.replace(nvram)
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
                f"--root-ssh-authorized-keys=/output/{OUTPUT.name}/id_ed25519.pub",
                "--skip-fetch-check", *target_options, f"/output/{OUTPUT.name}/disk.raw",
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
        "qemu-system-x86_64", "-machine", "q35,accel=kvm,smm=on", "-cpu", "host",
        "-global", "driver=cfi.pflash01,property=secure,value=on",
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
                if UPGRADE_FROM:
                    result = check_upgrade(ssh, qemu, log)
                    power_transition(ssh, "poweroff", log)
                    qemu.wait(timeout=60)
                    if qemu.returncode != 0:
                        raise RuntimeError(f"QEMU exited with {qemu.returncode}")
                    (OUTPUT / "result.json").write_text(json.dumps(result, indent=2) + "\n")
                    print(json.dumps(result, indent=2), flush=True)
                    return
                with (ROOT / "tests" / "guest-check.sh").open() as script:
                    run(ssh + ["bash -s -- untrusted"], stdin=script, stdout=log,
                        stderr=subprocess.STDOUT, timeout=180)
                power_transition(ssh, "poweroff", log)
                qemu.wait(timeout=60)
                if qemu.returncode != 0:
                    raise RuntimeError(f"Untrusted-key boot exited with {qemu.returncode}")
                shutil.copyfile(OUTPUT / "serial.log", OUTPUT / "untrusted-serial.log")
                enrolled = OUTPUT / "OVMF_VARS.enrolled.fd"
                run([
                    "virt-fw-vars", "--input", nvram, "--output", enrolled,
                    "--add-mok", "605dab50-e046-4300-abb6-3dd810dd8b23", cert,
                ])
                enrolled.replace(nvram)
                qemu = subprocess.Popen(command, stdout=qemu_log, stderr=subprocess.STDOUT)
                wait_ssh(ssh, qemu)
                with (ROOT / "tests" / "guest-check.sh").open() as script:
                    run(ssh + ["bash -s -- first"], stdin=script, stdout=log,
                        stderr=subprocess.STDOUT, timeout=180)
                old_boot = subprocess.check_output(
                    ssh + ["cat /proc/sys/kernel/random/boot_id"], text=True
                ).strip()
                power_transition(ssh, "reboot", log)
                wait_ssh(ssh, qemu, previous_boot=old_boot)
                with (ROOT / "tests" / "guest-check.sh").open() as script:
                    run(ssh + ["bash -s -- reboot"], stdin=script, stdout=log,
                        stderr=subprocess.STDOUT, timeout=180)
                power_transition(ssh, "poweroff", log)
                qemu.wait(timeout=60)
                if qemu.returncode != 0:
                    raise RuntimeError(f"QEMU exited with {qemu.returncode}")
            result = {
                "image": IMAGE,
                "image_id": image["Id"],
                "image_digest": image["Digest"],
                "result": "PASS",
                "checks": [
                    "Secure Boot enabled with kernel lockdown",
                    "Alma boots but ZFS is rejected before MOK enrollment",
                    "Signed ZFS loads after MOK enrollment",
                    "UEFI boot and reboot", "bootc deployment and immutable /usr",
                    "SELinux enforcing", "ZFS automatic module loading",
                    "ZFS pool IO, snapshot, clone, export/import, scrub",
                    "ZFS automatic import and data persistence after reboot",
                    "libvirt default NAT network", "nested KVM domain via libvirt",
                    "Docker and NFS disabled on boot; functional on demand",
                    "wg-quick creates and removes a working WireGuard interface",
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
