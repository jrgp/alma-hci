#!/usr/bin/env bash
set -euxo pipefail

kver="$(rpm -q kernel-core --qf '%{VERSION}-%{RELEASE}.%{ARCH}')"
key=/run/secrets/zfs_signing_key
cert=/run/secrets/zfs_signing_cert
test -r "$key"
test -r "$cert"
printf '%s  %s\n' "${ZFS_SIGNING_CERT_SHA256:?}" "$cert" | sha256sum -c -
cmp <(openssl pkey -in "$key" -pubout) \
    <(openssl x509 -inform DER -in "$cert" -pubkey -noout)
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
# OpenZFS's RPM hook signs after stripping/debug extraction. Keep the private
# key on its secret mount, never in the build context or a cached layer.
mkdir -p "/usr/src/kernels/${kver}/certs"
ln -sf "$key" "/usr/src/kernels/${kver}/certs/signing_key.pem"
ln -sf "$cert" "/usr/src/kernels/${kver}/certs/signing_key.x509"
mkdir -p /var/tmp/zfs-rpmbuild/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
rpmbuild --rebuild --define "kverrel ${kver}" \
    --define '_topdir /var/tmp/zfs-rpmbuild' \
    --define "_smp_mflags -j$(nproc)" /source/*.src.rpm
find /var/tmp/zfs-rpmbuild/RPMS -type f -name 'kmod-zfs-[0-9]*.rpm' \
    -exec cp -t /rpms {} +
test "$(find /rpms -name '*.rpm' | wc -l)" -eq 1
cp "$cert" /rpms/zfs.der
{
    printf 'kernel=%s\n' "$kver"
    sha256sum /source/*.src.rpm /rpms/*.rpm
    openssl x509 -inform DER -in "$cert" -noout -fingerprint -sha256
} > /rpms/kmod-build.txt
