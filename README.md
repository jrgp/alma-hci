# Alma HCI

Headless x86_64 AlmaLinux 10 bootc server images:
**`ghcr.io/jrgp/alma-hci:10`** (`latest` is an alias).
Derived from the [AlmaLinux atomic respin template](https://github.com/AlmaLinux/atomic-respin-template)
using the official `almalinux-bootc` server base, not a desktop.
There are no ISO builds.

Included: libvirt's modular daemons, QEMU/KVM, OVMF, `virsh`, `virt-install`,
OpenZFS `kmod-zfs` (never DKMS), Bash, `ip`/`bridge`, `brctl`,
NetworkManager/`nmcli`, `nmstatectl`, SSH client/server, chrony, and SELinux.
The nmstate command is **`nmstatectl`**, not `nmstatecli`.
`brctl` is included for compatibility; prefer NetworkManager or `bridge` for
new configurations.

The OS boots on **XFS**; ZFS is for data pools under `/var`, not root-on-ZFS.
The image enables `multi-user.target`, NetworkManager, SSH, libvirt and cached
ZFS pool import/mount services. It contains no built-in credentials.
The AlmaLinux bootc base is still experimental upstream.

ZFS kmods are compiled **during the image build** from signature-verified
official OpenZFS source RPMs into a kmod RPM for the exact bundled kernel.
No compiler, kernel-devel or DKMS is needed on installed hosts. This avoids
depending on the binary kmod repository catching up with a new Alma minor
release. Source/RPM checksums and the kernel version are recorded at
`/usr/share/alma-server/kmod-build.txt`; userspace uses `zfs-latest-kmod`.

## Install directly from GHCR

Use an x86_64 Linux installer/rescue environment with rootful Podman, skopeo,
Cosign, and loop-device support. Fedora's packages include Podman and skopeo;
install Cosign from its [official releases](https://github.com/sigstore/cosign/releases).
Clone this repository to obtain the trusted `cosign.pub`.
Never run an unverified image privileged.

Resolve and verify an immutable image before either installation example:

```sh
REPOSITORY=ghcr.io/jrgp/alma-hci
DIGEST=$(skopeo inspect --format '{{.Digest}}' "docker://$REPOSITORY:10")
IMAGE="$REPOSITORY@$DIGEST"
cosign verify --insecure-ignore-tlog=true --key ./cosign.pub "$IMAGE"
sudo podman pull "$IMAGE"
```

Signatures are verified directly against this project's public key; the
project does not upload its signatures to the public Rekor transparency log.
Cosign verification does not enable firmware Secure Boot.

GHCR packages initially default to private, independently of repository
visibility. If an anonymous pull is denied, the package owner can make it
public at [package settings](https://github.com/users/jrgp/packages/container/alma-hci/settings).
For a private package, authenticate Podman, skopeo and Cosign first using a
token with `read:packages`; provision registry credentials on the installed
host in `/etc/ostree/auth.json` for future bootc updates as well. Do not use
the short-lived CI `GITHUB_TOKEN` as a server's long-term credential.

### A physical block device

**DESTRUCTIVE: this erases the entire selected disk.** Boot a separate
installer environment, inspect `lsblk`, and replace the example by-id path
with the intended unused target. Do not target the running system's disk.
The SSH public key file must already exist; its private key stays with you.

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

The installed system follows the moving, signature-enforced `:10` reference,
even though installation uses the exact digest verified above.

### A raw .img file for a VM

The following creates a sparse disk file, never targets a physical disk, and
installs both bootloaders for a generic VM image. Use a new filename; `truncate`
does not reset an existing file's contents.

```sh
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
```

For example, attach the result as a raw virtio disk in libvirt/virt-install:

```sh
sudo virt-install --name alma-hci-test --memory 4096 --vcpus 2 \
  --import --os-variant almalinux10 --boot uefi --graphics none \
  --disk "path=$PWD/output/alma-hci.img,format=raw,bus=virtio" \
  --network network=default --noautoconsole
```

## Daily builds, signing, and change detection

[GitHub Actions](https://github.com/jrgp/alma-hci/actions/workflows/build.yml)
checks daily at **08:37 UTC**, on non-documentation pushes to `main`, and on
manual dispatch. Scheduled jobs can be delayed by GitHub. A lightweight check
must run to discover upstream changes; GitHub cannot skip the whole scheduled
workflow based on external RPM/registry state.

The check verifies AlmaLinux's signature on the current upstream `:10` digest.
It compares that digest and a hash of the build inputs/tests with the last
signed published image. If those match, it refreshes RPM metadata in the last
image and checks for installed userspace package updates. When nothing changed,
**the build/VM/publish job is skipped and no new images are pushed**.
Network, authentication, and RPM repository failures fail the check rather
than being reported as "no changes".

On changes, CI pins the resolved upstream digest for that build, upgrades
userspace, and runs the image checks and two-boot KVM/ZFS test suite. Kernels
and their initramfs come only from the upstream bootc base, never a separate
DNF kernel upgrade. A new EL minor release can break ZFS kABI compatibility;
a failing source/module build or boot test leaves stable tags on the previous
image. Third-party prebuilt kmods may lag a new kernel ABI, which is why this
project builds its kmod RPM for the actual base kernel instead.

Only trusted `main` builds publish. CI pushes a unique candidate, demonstrates
that the installed image's containers/image policy **rejects it unsigned**,
signs the exact digest, then demonstrates that the same policy **accepts it**.
Only after those checks do `:10` and `:latest` advance. The immutable
`build-RUN_ID-ATTEMPT` candidate tag is retained as a version reference.
Pull requests never receive the signing secret or publish images.

The private key is the encrypted GitHub Actions repository secret
**`SIGNING_SECRET`**. Only `cosign.pub` is in Git/the image. Signing uses
Cosign 2.6.5's legacy `.sig` attachment because that is the format consumed by
bootc's containers/image verification. The workflow verifies this format, not
just Cosign's own success.

To recheck manually, or force recovery/initial publication:

```sh
gh workflow run build.yml --repo jrgp/alma-hci
gh workflow run build.yml --repo jrgp/alma-hci -f force=true
```

CI uploads non-secret logs, exact tested/published digests, signature evidence,
and package inventory as a 14-day Actions artifact. Private keys and VM disks
are excluded. Enable Actions failure notifications/watch the repository so a
failed daily build does not leave hosts silently behind.

## Build and test locally

```sh
sudo dnf install podman skopeo make python3 qemu-kvm qemu-img edk2-ovmf openssh-clients
make image
make test
```

The default local image is `localhost/alma-hci:10`, in rootful Podman storage.
Its update/signature policy still targets `ghcr.io/jrgp/alma-hci:10`.
For another local tag, pass `IMAGE_NAME=localhost/example:10` to each target.
The Dockerfile has a reproducible fallback base digest; CI overrides it with
the current signature-verified major-version tag resolved to a digest.
`BASE_IMAGE=...@sha256:...` can override the base for a local build too.

`make test-image` checks bootc lint, packages/tools, enabled services, lack
of a desktop/DKMS, repository choice and module resolution.
`make test-vm` installs into a generated sparse raw disk with the signed update
policy enforced. It checks UEFI boot, immutable `/usr`, SELinux enforcing,
NetworkManager, libvirt NAT, automatic ZFS loading, real pool IO,
snapshots/clones/export/import, nested KVM through libvirt, then reboots and
checks automatic pool import/mount, persistent data, and a scrub.

The VM test requires `/dev/kvm`, nested virtualization, OVMF, 3 GiB free RAM,
and at least 8 GiB free disk space. Software emulation is not a substitute.
It never loads AlmaLinux kernel modules into the host. It replaces only its
own dedicated test disks. QEMU is stopped on exit. Root-only test artifacts
are retained under ignored `output/vm/`:

```sh
sudo cat output/vm/result.json
sudo tail -100 output/vm/checks.log
```

Change-detection tests use only the Python standard library:

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

## Operate the server

```sh
sudo bootc status
sudo bootc upgrade
# Reboot in a maintenance window after shutting down/migrating guests.
sudo systemctl reboot
# If needed:
sudo bootc rollback
```

Publishing images does not update or reboot an installed host. Arrange staged
rollouts, maintenance-window reboots, canaries and stale-host monitoring for
your fleet. Do not enable unattended rebooting of active virtualization hosts.
The tests do not yet certify image upgrade/rollback or physical hardware.

Use `sudo virsh -c qemu:///system` locally or `qemu+ssh://USER@HOST/system`
remotely. No unauthenticated libvirt TCP listener is enabled. Configure
production bridges with NetworkManager/nmstate; the packaged default libvirt
network is NAT.

Create ZFS data pools on explicitly selected unused disks with persistent
mountpoints and a cache file:

```sh
# DESTRUCTIVE example: replace the disk with your intended unused data disk.
sudo zpool create -o ashift=12 -o cachefile=/etc/zfs/zpool.cache \
  -O compression=lz4 -O mountpoint=/var/lib/tank tank /dev/disk/by-id/YOUR-DATA-DISK
```

Keep VM storage under `/var`, preserve SELinux labels, and configure
redundancy, backups, encryption and scrubs to suit your storage. The respin
recreates libvirt's RPM-owned `/var` directories with correct permissions and
SELinux labels and orders ZFS mounts after bootc's writable `/var` setup.

**Secure Boot is separate from image signing.** OpenZFS kmods are third-party
modules; this project has not implemented module signing/key enrollment or
tested Secure Boot. Do not assume the signed container makes them trusted by
firmware/the kernel.
