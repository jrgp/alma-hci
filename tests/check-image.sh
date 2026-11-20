#!/usr/bin/env bash
set -euo pipefail

bootc container lint
rpm -q bootc kernel-core zfs kmod-zfs libvirt-daemon-kvm libvirt-client \
    qemu-kvm virt-install bash iproute bridge-utils NetworkManager nmstate \
    openssh-clients openssh-server mokutil openssl
cmp /expected-zfs.der /usr/share/alma-hci/zfs.der
openssl x509 -inform DER -in /usr/share/alma-hci/zfs.der -noout -subject -fingerprint -sha256
expected_key="$(openssl x509 -inform DER -in /usr/share/alma-hci/zfs.der -noout -serial \
    | cut -d = -f 2 | tr '[:lower:]' '[:upper:]')"
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
modules=0
while IFS= read -r module; do
    [[ -n "$(modinfo -F signer "$module")" ]]
    [[ "$(modinfo -F sig_key "$module" | tr -d : | tr '[:lower:]' '[:upper:]')" == "$expected_key" ]]
    [[ "$(modinfo -F sig_hashalgo "$module")" == sha256 ]]
    modules=$((modules + 1))
done < <(rpm -ql kmod-zfs | grep -E '\.ko(\.(xz|gz|zst))?$')
(( modules > 0 ))
echo "PASS: image checks (module loading requires the VM test)"
