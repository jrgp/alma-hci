# Alma HCI

Headless AlmaLinux 10 bootc server: **`ghcr.io/jrgp/alma-hci:10`**
(`latest` is an alias). Based on the
[AlmaLinux atomic respin template](https://github.com/AlmaLinux/atomic-respin-template).

Includes libvirt/KVM, QEMU, OVMF, `virsh`, `virt-install`, OpenZFS, Bash,
`ip`/`bridge`, `brctl`, NetworkManager/`nmcli`, `nmstatectl`, SSH/sshd,
chrony and SELinux. ZFS kmods are built for the bundled kernel during the
image build: **no DKMS or compiler on installed hosts**.
The OS uses XFS; keep ZFS data and VM storage under `/var`.

## Quickstart

Use an x86_64 Linux installer/rescue environment with rootful Podman,
[Cosign](https://github.com/sigstore/cosign/releases), `jq`, and your SSH
public key at `~/.ssh/id_ed25519.pub`. No repository checkout or registry
login is needed.

Verify the image once before either install below. This fetches the public
key directly and automatically selects the verified image:

```sh
IMAGE=$(cosign verify --insecure-ignore-tlog=true \
  --key https://raw.githubusercontent.com/jrgp/alma-hci/main/cosign.pub \
  ghcr.io/jrgp/alma-hci:10 |
  jq -er '"ghcr.io/jrgp/alma-hci@" + .[0].critical.image["docker-manifest-digest"]')
```

### Install to a disk

**Erases the entire target disk.** Check `lsblk`, replace the target below,
and never select the disk running your installer.

```sh
sudo podman run --rm --privileged --pid=host \
  --security-opt=label=disable \
  -v /var/lib/containers/storage:/var/lib/containers/storage \
  -v /dev:/dev \
  -v "$HOME/.ssh/id_ed25519.pub:/run/admin.pub:ro" \
  "$IMAGE" bootc install to-disk --wipe \
  --target-imgref=ghcr.io/jrgp/alma-hci:10 \
  --enforce-container-sigpolicy \
  --root-ssh-authorized-keys=/run/admin.pub \
  /dev/disk/by-id/REPLACE-WITH-YOUR-TARGET-DISK
```

### Install to a VM disk image

Creates a new sparse 20 GiB raw image; requires loop-device support.

```sh
(
set -eu
umask 077
mkdir -p output
test ! -e output/alma-hci.img
truncate -s 20G output/alma-hci.img
sudo podman run --rm --privileged --pid=host \
  --security-opt=label=disable \
  -v /var/lib/containers/storage:/var/lib/containers/storage \
  -v /dev:/dev \
  -v "$PWD/output:/output" \
  -v "$HOME/.ssh/id_ed25519.pub:/run/admin.pub:ro" \
  "$IMAGE" bootc install to-disk --via-loopback --generic-image \
  --karg=console=ttyS0,115200n8 \
  --target-imgref=ghcr.io/jrgp/alma-hci:10 \
  --enforce-container-sigpolicy \
  --root-ssh-authorized-keys=/run/admin.pub \
  /output/alma-hci.img
)
```

Attach `output/alma-hci.img` as a raw virtio disk to a UEFI VM, or use:

```sh
sudo virt-install --name alma-hci-test --memory 4096 --vcpus 2 \
  --import --os-variant almalinux10 --boot uefi --graphics none \
  --disk "path=$PWD/output/alma-hci.img,format=raw,bus=virtio" \
  --network network=default --noautoconsole
```

## Updates

Installed hosts follow the signed `:10` image. Updates do not reboot hosts;
shut down or migrate guests before rebooting.

```sh
sudo bootc upgrade
sudo systemctl reboot
# To undo an update:
sudo bootc rollback
```

[CI](https://github.com/jrgp/alma-hci/actions/workflows/build.yml) checks
upstream daily at **08:37 UTC**. Unchanged base, build inputs and installed
RPMs skip the build and push. Code changes always build and test.
Publication requires two-boot ZFS/KVM tests with SELinux enforcing, plus
unsigned-image rejection and signed-image acceptance.
The private key stays in Actions secret `SIGNING_SECRET`; only
[`cosign.pub`](cosign.pub) is public. Signatures use legacy Cosign attachments,
without Rekor transparency-log uploads.

Watch Actions for failures or disabled schedules: GitHub may disable scheduled
workflows after 60 days of repository inactivity.

## Local development

From a source checkout, run `make image` then `make test`.
Tests require QEMU/KVM with nested virtualization, OVMF, 3 GiB free RAM and
8 GiB free disk space. Evidence is written under `output/vm/`.

## Important limits

The upstream bootc base is experimental. Secure Boot/module signing and
image upgrade/rollback tests are not implemented.
The default container policy trusts only this project's signed repository;
add explicit policies for other registries you use.
Configure production networking with NetworkManager/nmstate, and manage
libvirt locally with `sudo virsh -c qemu:///system` or over SSH.
Plan storage redundancy, backups, scrubs and staged host updates.
