# Alma HCI

An **atomic variant of AlmaLinux 10** for homelabs and servers running VMs,
containers, NAS workloads and similar services.

- Headless: no desktop or GUI.
- Image-based updates with bootc; changes take effect when you reboot.
- Published image: **`ghcr.io/jrgp/alma-hci:10`** (`latest` is an alias).
- x86_64 only. The upstream Alma bootc base is experimental.

## What's included

- **VMs:** libvirt/KVM, QEMU, OVMF, `virsh` and `virt-install`.
- **Containers:** Podman, Toolbox, official Docker CE, Compose and Buildx.
- **Storage:** OpenZFS with signed, prebuilt kernel modules; no DKMS or host compiler.
- **File sharing:** NFS server tools.
- **Networking:** NetworkManager, nmstate, `ip`/`bridge`, `brctl` and WireGuard/`wg-quick`.
- **Administration:** SSH, Bash, Git, chrony and SELinux.
- **System disk:** XFS; keep ZFS data and VM storage under `/var`.

## Install

- Use an x86_64 Linux installer/rescue environment with rootful Podman,
  [Cosign](https://github.com/sigstore/cosign/releases) and `jq`.
- Have your SSH public key at `~/.ssh/id_ed25519.pub`.
- No repository clone or registry login is needed.
- Verify and select the signed image:

```sh
IMAGE=$(cosign verify --insecure-ignore-tlog=true \
  --key https://raw.githubusercontent.com/jrgp/alma-hci/main/cosign.pub \
  ghcr.io/jrgp/alma-hci:10 |
  jq -er '"ghcr.io/jrgp/alma-hci@" + .[0].critical.image["docker-manifest-digest"]')
```

- **The following erases the entire target disk.** Check `lsblk` and replace
  the target; never select the disk running your installer.

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

- For a VM disk image instead, see [VM installation](ARCHITECTURE.md#vm-installation).

## Secure Boot and ZFS

- Alma's signed kernel and bootloader are unchanged.
- The OS boots with Secure Boot, but ZFS needs one-time enrollment of our module key.
- Run inside the installed system:

```sh
sudo mokutil --import /usr/share/alma-hci/zfs.der
sudo systemctl reboot
```

- Set a temporary password when prompted.
- At the machine/VM console, choose **Enroll MOK** and confirm with that password.
- Reboot again; ZFS loads automatically. Check with
  `mokutil --test-key /usr/share/alma-hci/zfs.der` and `lsmod | grep '^zfs'`.
- Keep the VM's NVRAM; resetting it may require enrollment again.

## Updates

- Automatic updates **download and stage, never automatically reboot**.
- Shut down or migrate running VMs before manually rebooting.
- To update now:

```sh
sudo bootc upgrade
sudo systemctl reboot
```

- To roll back: `sudo bootc rollback`, then reboot.
- CI checks upstream daily at **08:37 UTC** and publishes only after its tests pass.
- [Release notes](https://github.com/jrgp/alma-hci/releases) include the upstream
  Alma bootc version, dated image tag and RPM changes.

## Optional services

- Docker/containerd and NFS/RPC are installed but **disabled by default**.
- Configure `/etc/exports` before enabling NFS.
- WireGuard is available; no tunnels are configured.

```sh
sudo systemctl enable --now docker
sudo systemctl enable --now nfs-server
```

- Podman/bootc trusts only this project's signed registry images by default;
  authorize other image repositories explicitly. Docker does not use that policy.
- Plan backups, storage redundancy and maintenance windows for your own workloads.
- See [architecture, CI tests and local development](ARCHITECTURE.md) for details.
