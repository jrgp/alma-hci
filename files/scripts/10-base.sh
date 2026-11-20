#!/usr/bin/env bash

set -xeuo pipefail

[[ "$(rpm --eval '%{_arch}')" == x86_64 ]]
[[ "$(rpm --eval '%{rhel}')" == 10 ]]

dnf install -y dnf-plugins-core epel-release \
    https://zfsonlinux.org/epel/zfs-release-3-0.el10.noarch.rpm
dnf config-manager --set-disabled 'zfs*'
dnf config-manager --set-enabled zfs-latest-kmod
dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo

# The kernel and its initramfs come from the verified bootc base. Upgrade
# userspace here, but never mix in a different kernel from the RPM mirrors.
dnf upgrade -y --refresh --setopt=install_weak_deps=False \
    --exclude='kernel*' --exclude=dkms,zfs-dkms
dnf install -y --setopt=install_weak_deps=False --exclude='kernel*' --exclude=dkms,zfs-dkms \
    zfs /zfs-rpms/kmod-zfs-*.rpm \
    libvirt-daemon-kvm libvirt-client libvirt-daemon-config-network \
    virt-install qemu-kvm qemu-img edk2-ovmf \
    bash git iproute bridge-utils NetworkManager nmstate \
    openssh-clients openssh-server chrony mokutil openssl \
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin \
    nfs-utils rpcbind wireguard-tools

install -D -m 0644 /zfs-rpms/zfs.der /usr/share/alma-hci/zfs.der

# cleanup.sh discards /var. Recreate the RPM-owned libvirt directories at
# boot with their original permissions and the policy's SELinux labels.
rpm -qa 'libvirt*' --qf '%{NAME}\n' \
    | xargs rpm -q --dump \
    | awk '$1 ~ /^\/var\// && $5 ~ /^04/ {
        print "d", $1, substr($5, length($5) - 3), $6, $7, "-"
    }' \
    | sort -u > /usr/lib/tmpfiles.d/alma-libvirt.conf

# The prebuilt kmod may live in another kernel's extra/ directory and be
# referenced by weak-updates. Keep both; check the kernel we actually boot.
for kernel in /usr/lib/modules/*/vmlinuz; do
    kver="$(basename "$(dirname "$kernel")")"
    depmod -a "$kver"
    modinfo -k "$kver" -F filename zfs
    modinfo -k "$kver" -F version zfs
    modprobe --set-version "$kver" --show-depends zfs
    modprobe --set-version "$kver" --show-depends kvm_amd
    modprobe --set-version "$kver" --show-depends kvm_intel
done

systemctl set-default multi-user.target
systemctl enable NetworkManager.service sshd.service chronyd.service \
    virtqemud.service virtnetworkd.service virtstoraged.service \
    virtqemud.socket virtnetworkd.socket virtstoraged.socket \
    virtnodedevd.socket virtnwfilterd.socket virtsecretd.socket \
    virtlogd.socket virtlockd.socket virtproxyd.socket \
    zfs.target zfs-import-cache.service zfs-mount.service zfs-zed.service
systemctl disable docker.service docker.socket containerd.service \
    nfs-server.service nfs-client.target rpcbind.service rpcbind.socket \
    wg-quick@.service

mkdir -p /usr/share/alma-server
cp /zfs-rpms/kmod-build.txt /usr/share/alma-server/kmod-build.txt
rpm -qa --qf '%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\n' \
    | sort > /usr/share/alma-server/packages.txt
if rpm -qa --qf '%{NAME}\n' | grep -Ex 'dkms|zfs-dkms|gdm|sddm|gnome-shell|plasma-desktop|xorg-x11-server-Xorg'; then
    echo "Unexpected DKMS or desktop package in server image" >&2
    exit 1
fi
