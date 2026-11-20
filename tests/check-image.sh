#!/usr/bin/env bash
set -euo pipefail

bootc container lint
rpm -q bootc kernel-core zfs kmod-zfs libvirt-daemon-kvm libvirt-client \
    qemu-kvm virt-install bash iproute bridge-utils NetworkManager nmstate \
    openssh-clients openssh-server
ip -Version
brctl --version
nmcli --version
nmstatectl --version
bash --version
ssh -V
sshd -V
if rpm -qa --qf '%{NAME}\n' | grep -Ex 'dkms|zfs-dkms|gdm|sddm|gnome-shell|plasma-desktop|xorg-x11-server-Xorg'; then
    echo "DKMS or a desktop was installed" >&2
    exit 1
fi
[[ "$(systemctl get-default)" == multi-user.target ]]
[[ "$(cat /usr/lib/modules-load.d/alma-server-zfs.conf)" == zfs ]]
systemctl is-enabled virtqemud.service virtnetworkd.service \
    virtstoraged.service virtqemud.socket virtproxyd.socket \
    zfs.target zfs-import-cache.service zfs-mount.service zfs-zed.service \
    NetworkManager.service sshd.service
dnf repolist --enabled | grep -E '^zfs-latest-kmod[[:space:]]'
if dnf repolist --enabled | grep -E '^zfs[[:space:]]'; then
    echo "The DKMS repository is enabled" >&2
    exit 1
fi
for kernel in /usr/lib/modules/*/vmlinuz; do
    kver="$(basename "$(dirname "$kernel")")"
    modinfo -k "$kver" -F filename zfs
    modinfo -k "$kver" -F version zfs
    modprobe --set-version "$kver" --show-depends zfs
    modprobe --set-version "$kver" --show-depends kvm_amd
    modprobe --set-version "$kver" --show-depends kvm_intel
done
echo "PASS: image checks (module loading requires the VM test)"
