# Architecture and development

Alma HCI is based on the
[AlmaLinux atomic respin template](https://github.com/AlmaLinux/atomic-respin-template).
See the [README](README.md) for the image's purpose, bundled tools and basic installation.

## Image build

- The runtime image extends AlmaLinux's `almalinux-bootc:10` image.
- CI resolves the current upstream tag to an immutable digest and verifies it
  with Alma's [`bootc.pub`](bootc.pub) before building.
- The Dockerfile's hardcoded digest is a reproducible local default, not a CI pin.
  CI overrides it through the `BASE_IMAGE` build argument.
- An isolated build stage produces the ZFS kmod RPM; the runtime stage installs
  it alongside the server packages and configuration.
- Userspace RPMs are upgraded during the build. `kernel*` is excluded: the
  verified base owns the kernel and its initramfs.
- The system disk uses XFS. ZFS is for data, not the root filesystem.

## ZFS and Secure Boot

- The kmod is built from an official, GPG-verified OpenZFS source RPM.
- Kernel development headers must exactly match the bundled kernel.
- Dependency resolution cannot upgrade the kernel or its headers independently.
- Modules are signed after stripping, with checks for their signatures and vermagic.
- Only our ZFS modules use the project module-signing key. Alma's kernel and
  bootloader retain their vendor signatures.
- Installed hosts need no DKMS or build toolchain.
- Before MOK enrollment, Alma boots but the kernel rejects ZFS.
- Enrollment is once per machine/VM. Preserve NVRAM across image updates.

## Updates and releases

- Installed hosts track the signed `ghcr.io/jrgp/alma-hci:10` image.
- The enabled `bootc-fetch-apply-updates.timer` runs `bootc upgrade --quiet`
  through a service drop-in: updates stage for the next manual reboot.
- There is no automatic VM draining or maintenance-window orchestration.
- CI checks upstream daily at 08:37 UTC. It builds when the base digest,
  tracked build inputs or installed userspace RPMs change.
- Pushes and pull requests force a build; an unchanged daily check skips publication.
- Published Git/image tags use `10-YYYYMMDD.RUN_NUMBER.RUN_ATTEMPT`.
  Multiple releases can point to the same source commit.
- Release notes identify the exact upstream Alma bootc version and digest-pinned
  reference used by that build, plus RPM additions, removals and version changes.
- Package comparisons use the actual previous and candidate image inventories,
  including architectures, epochs and multiple installed versions.
- Release assets include `packages.json`, `published.json` and `signature.json`.
- Registry-tag promotion and GitHub release creation are sequential operations,
  not a cross-service atomic transaction.

## Publication gates

The [workflow](.github/workflows/build.yml) requires:

- Image lint, requested packages, service defaults and matching signed ZFS modules.
- A Secure Boot/lockdown VM that boots Alma but rejects ZFS before MOK enrollment.
- Two trusted ZFS/KVM boots with SELinux enforcing.
- ZFS IO, snapshots, clones, export/import, scrub and reboot persistence.
- A nested KVM guest through libvirt and its default NAT network.
- On-demand Docker, NFS and WireGuard checks; optional services remain off at boot.
- Rejection of an unsigned registry candidate, followed by acceptance after signing.
- An upgrade from the immediately previous published digest before stable tags move.

The upgrade VM:

- Boots the previous signed release with only that release's ZFS MOK enrolled.
- Creates a ZFS pool, payload, checksum, snapshot and persistent `/etc` marker.
- Uses `bootc switch --enforce-container-sigpolicy` to stage the immutable candidate.
  This uses the same deployment operation as `upgrade`, while changing the reference
  without moving production tags.
- Confirms the old image is still running and the new image is staged, without a reboot.
- Reboots and requires a new boot ID and the exact candidate digest, not a fallback.
- Checks the old deployment remains available for rollback.
- Requires ZFS autoload, pool auto-import, preserved data/snapshot, new writes and a scrub.

Evidence is uploaded from `output/vm/` and `output/vm-upgrade/`; private keys,
SSH keys, VM disks and firmware-variable files are not uploaded.
Pull requests run fresh-install tests with a throwaway module key and do not publish.
The signed upgrade gate runs on main when publishing; the first-ever publication
has no previous image and cannot run that gate.

## Trust and limits

- Container and ZFS module signing use separate keys.
- Private keys stay in Actions secrets `SIGNING_SECRET` and `ZFS_MODULE_SIGNING_KEY`.
  Only [`cosign.pub`](cosign.pub) and [`zfs-signing.der`](zfs-signing.der) are public.
- Container signatures use legacy Cosign attachments for bootc/containers-image
  compatibility, without Rekor transparency-log uploads.
- `packages.json` is an RPM inventory, not a separately signed SPDX SBOM.
- Podman/bootc accepts this project's signed registry images and local image transports.
  Other registries need explicit policy entries; Docker does not consume this policy.
- The Alma bootc base is experimental; CI success is not a production-support guarantee.
- Upgrade tests cover the immediately previous release, not every older version.
  Actually booting a rollback is not tested.
- Workload backups, monitoring, physical-hardware qualification and rollout policies
  remain the operator's responsibility.
- Monitor Actions for failures or stale images. GitHub may disable scheduled workflows
  after 60 days of repository inactivity.

## VM installation

After selecting the verified `IMAGE` using the [README](README.md#install):

- Use a Linux host with rootful Podman and loop-device support.
- The following creates a new sparse 20 GiB raw disk without overwriting an existing file.

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

- Attach the raw virtio disk to a UEFI VM, or import it with libvirt:

```sh
sudo virt-install --name alma-hci-test --memory 4096 --vcpus 2 \
  --import --os-variant almalinux10 --boot uefi --graphics none \
  --disk "path=$PWD/output/alma-hci.img,format=raw,bus=virtio" \
  --network network=default --noautoconsole
```

- For Secure Boot, use Secure Boot-capable firmware and enroll the ZFS MOK
  following the README. Preserve the VM's NVRAM.
- Configure production networking with NetworkManager/nmstate; manage libvirt
  with `sudo virsh -c qemu:///system` or over SSH.

## Local development

- From a source checkout, run `make image` then `make test`.
- Supply a matching PEM private key and DER certificate through
  `ZFS_SIGNING_KEY` and `ZFS_SIGNING_CERT`; the production private key is not public.
- Override `BASE_IMAGE` to test a different digest-pinned upstream base.
- Set `SOURCE_HASH` to a new build-input fingerprint when rebuilding changed
  local inputs, to avoid reusing the bound-context runtime build cache.
- Tests require QEMU/KVM with nested virtualization, Secure Boot OVMF firmware,
  `virt-fw-vars`, 3 GiB free RAM and 8 GiB free disk space.
- Results and logs are written under `output/vm/`.

To test two distinct, signed published images:

```sh
make test-upgrade \
  UPGRADE_FROM_IMAGE=ghcr.io/jrgp/alma-hci@sha256:OLD_DIGEST \
  UPGRADE_TO_IMAGE=ghcr.io/jrgp/alma-hci@sha256:NEW_DIGEST
```

- Allow additional disk space for the previous image and both deployments.
- Upgrade evidence, including booted/staged digest records, is in `output/vm-upgrade/`.
