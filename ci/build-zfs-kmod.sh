#!/usr/bin/env bash
set -euxo pipefail

kver="$(rpm -q kernel-core --qf '%{VERSION}-%{RELEASE}.%{ARCH}')"
dnf install -y --setopt=install_weak_deps=False \
    dnf-plugins-core epel-release rpm-build redhat-rpm-config kernel-rpm-macros \
    "kernel-devel-${kver}" gcc make elfutils-libelf-devel \
    https://zfsonlinux.org/epel/zfs-release-3-0.el10.noarch.rpm
dnf config-manager --set-disabled 'zfs*'
dnf config-manager --set-enabled zfs-latest-kmod
mkdir -p /source /rpms
dnf --enablerepo=zfs-latest-source download --source --destdir=/source zfs-kmod
rpm --import /etc/pki/rpm-gpg/RPM-GPG-KEY-openzfs-el-10
rpmkeys --checksig /source/*.src.rpm
dnf builddep -y /source/*.src.rpm
mkdir -p /var/tmp/zfs-rpmbuild/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
rpmbuild --rebuild --define "kverrel ${kver}" \
    --define '_topdir /var/tmp/zfs-rpmbuild' \
    --define "_smp_mflags -j$(nproc)" /source/*.src.rpm
find /var/tmp/zfs-rpmbuild/RPMS -type f -name 'kmod-zfs-[0-9]*.rpm' \
    -exec cp -t /rpms {} +
test "$(find /rpms -name '*.rpm' | wc -l)" -eq 1
{
    printf 'kernel=%s\n' "$kver"
    sha256sum /source/*.src.rpm /rpms/*.rpm
} > /rpms/kmod-build.txt
