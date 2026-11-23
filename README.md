# Alma HCI

Headless AlmaLinux 10 bootc server: **`ghcr.io/jrgp/alma-hci:10`**
(`latest` is an alias). Based on the
[AlmaLinux atomic respin template](https://github.com/AlmaLinux/atomic-respin-template).

Includes libvirt/KVM, QEMU, OVMF, `virsh`, `virt-install`, OpenZFS, Git, Bash, Toolbox,
`ip`/`bridge`, `brctl`, NetworkManager/`nmcli`, `nmstatectl`, SSH/sshd,
Docker CE/Compose/Buildx, NFS server tools, WireGuard/`wg-quick`, chrony and
SELinux. ZFS kmods are built for the bundled kernel during the
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

## Trust the ZFS modules with Secure Boot

Alma's signed kernel and bootloader are unchanged: the OS can boot before
enrollment, but ZFS cannot load until you trust its separate signing key.
On an existing installation, run `sudo bootc upgrade` and reboot first to
get the signed modules and certificate.

Inside the installed system:

```sh
sudo mokutil --import /usr/share/alma-hci/zfs.der
sudo systemctl reboot
```

Choose a temporary password when importing. At the machine/VM console,
select **Enroll MOK**, confirm enrollment using that password, then reboot.
ZFS loads automatically; confirm with:

```sh
mokutil --test-key /usr/share/alma-hci/zfs.der
mokutil --sb-state
lsmod | grep '^zfs'
```

Enrollment is once per machine/VM, not per image update. Preserve the VM's
NVRAM; replacement VMs or reset firmware may need enrollment again.
Only our ZFS modules use this key, not Alma's kernel or stock modules.

## Optional services

Docker CE comes from Docker's official RPM repository. Docker, containerd,
NFS and RPC activation are **disabled by default**; WireGuard has no configured
tunnels. Enable only what you need:

```sh
sudo systemctl enable --now docker
# Configure /etc/exports before starting an NFS server:
sudo systemctl enable --now nfs-server
```

Docker does not enforce the Podman/bootc container-signature policy.

## Updates

Installed hosts follow the signed `:10` image. Scheduled updates download and
stage the new image without rebooting (`bootc upgrade --quiet`). It takes
effect at the next manual reboot; shut down or migrate guests first.

```sh
sudo bootc upgrade
sudo systemctl reboot
# To undo an update:
sudo bootc rollback
```

[CI](https://github.com/jrgp/alma-hci/actions/workflows/build.yml) checks
upstream daily at **08:37 UTC**. Unchanged base, build inputs and installed
RPMs skip the build and push. Code changes always build and test.
Each published build gets a matching Git/image tag such as
`10-20261120.42.1` (UTC date, workflow run number, attempt).
[Releases](https://github.com/jrgp/alma-hci/releases) show RPM additions,
removals and version changes against the previous published image, with a
complete package inventory attached. Rebuilds can share the same source commit.
Publication requires Secure Boot tests rejecting ZFS before enrollment and
two ZFS/KVM boots after enrollment, with SELinux enforcing. Container-policy
tests also reject unsigned images and accept signed ones.
Before moving `:10`/`latest` or creating a release, CI also boots the previous
published digest in a Secure Boot VM, creates a ZFS pool and snapshot, stages
the signed candidate, and reboots. It requires the exact new digest to be
booted (not merely staged or fallen back), with ZFS automatically loaded and
the existing pool, data and snapshot intact. Only the previous image's MOK
is enrolled; updates cannot silently depend on enrolling another key.
The first-ever publication has no previous image, so that gate is not applicable.
Private keys stay in Actions secrets `SIGNING_SECRET` (containers) and
`ZFS_MODULE_SIGNING_KEY` (modules); only [`cosign.pub`](cosign.pub) and
[`zfs-signing.der`](zfs-signing.der) are public. Container signatures use legacy
Cosign attachments, without Rekor transparency-log uploads.

Watch Actions for failures or disabled schedules: GitHub may disable scheduled
workflows after 60 days of repository inactivity.

## Local development

From a source checkout, run `make image` then `make test`.
Builds require a matching PEM private key and DER certificate, supplied as
`ZFS_SIGNING_KEY` and `ZFS_SIGNING_CERT`; the project private key is not public.
CI uses a throwaway key for pull requests, never the production key.
Tests require QEMU/KVM with nested virtualization, Secure Boot OVMF firmware,
`virt-fw-vars`, 3 GiB free RAM and 8 GiB free disk space.
Evidence is written under `output/vm/`.

To test an upgrade between two distinct, signed published digests:

```sh
make test-upgrade \
  UPGRADE_FROM_IMAGE=ghcr.io/jrgp/alma-hci@sha256:OLD_DIGEST \
  UPGRADE_TO_IMAGE=ghcr.io/jrgp/alma-hci@sha256:NEW_DIGEST
```

This uses `bootc switch --enforce-container-sigpolicy` to stage the exact
candidate without moving production tags; `switch` uses the same deployment
operation as `upgrade` while changing the tracked reference.
Evidence, including booted/staged digest records, is in `output/vm-upgrade/`.
Allow additional disk space for the previous image and both VM deployments.
Pull requests run the fresh-install tests; the signed upgrade gate runs on
main when publishing, before promotion.

## Important limits

The upstream bootc base is experimental. The upgrade gate tests the immediately
previous release, not every older release. Booting a rollback is not tested.
The default container policy trusts only this project's signed repository;
add explicit policies for other registries you use.
Configure production networking with NetworkManager/nmstate, and manage
libvirt locally with `sudo virsh -c qemu:///system` or over SSH.
Plan storage redundancy, backups, scrubs and staged host updates.
