#!/usr/bin/env bash
set -euxo pipefail

stage="${1:?expected baseline or upgraded}"
[[ "$stage" == baseline || "$stage" == upgraded ]]
[[ "$(getenforce)" == Enforcing ]]
mokutil --sb-state | grep -x 'SecureBoot enabled'
grep -E '\[(integrity|confidentiality)\]' /sys/kernel/security/lockdown
mokutil --test-key /usr/share/alma-hci/zfs.der
uname -r
rpm -q kernel-core kmod-zfs zfs
[[ "$(modinfo -F vermagic zfs)" == "$(uname -r) "* ]]
# Check autoload before doing anything that could load the module for us.
test -d /sys/module/zfs
test -c /dev/zfs
zfs version

if [[ "$stage" == baseline ]]; then
    systemctl stop bootc-fetch-apply-updates.timer bootc-fetch-apply-updates.service
    test_disk=/dev/disk/by-id/virtio-alma-zfs-test
    test -b "$test_disk"
    test "$(lsblk -dnro TYPE "$test_disk")" = disk
    if findmnt --source "$test_disk"; then
        echo "Refusing to use a mounted test disk" >&2
        exit 1
    fi
    zpool create -o ashift=12 -o cachefile=/etc/zfs/zpool.cache \
        -O mountpoint=/var/lib/alma-upgrade-test alma-upgrade "$test_disk"
    zfs create alma-upgrade/data
    dd if=/dev/urandom of=/var/lib/alma-upgrade-test/data/payload bs=1M count=32 conv=fsync
    sha256sum /var/lib/alma-upgrade-test/data/payload > /var/tmp/upgrade-payload.sha256
    zfs snapshot alma-upgrade/data@before-upgrade
    printf 'preserve across bootc upgrade\n' > /etc/alma-upgrade-test
else
    [[ "$(cat /etc/alma-upgrade-test)" == 'preserve across bootc upgrade' ]]
    systemctl is-active zfs-import-cache.service zfs-mount.service
    zpool list -H -o health alma-upgrade | grep -x ONLINE
    [[ "$(findmnt -no FSTYPE /var/lib/alma-upgrade-test/data)" == zfs ]]
    sha256sum -c /var/tmp/upgrade-payload.sha256
    zfs list -t snapshot alma-upgrade/data@before-upgrade
    zfs clone alma-upgrade/data@before-upgrade alma-upgrade/clone
    cmp /var/lib/alma-upgrade-test/data/payload /var/lib/alma-upgrade-test/clone/payload
    zfs destroy alma-upgrade/clone
    dd if=/dev/urandom of=/var/lib/alma-upgrade-test/data/after-upgrade bs=1M count=4 conv=fsync
    zpool scrub alma-upgrade
    zpool wait -t scrub alma-upgrade
    zpool status -x alma-upgrade | grep 'is healthy'
    if journalctl -k -b --no-pager | grep -Ei '(zfs|spl):.*(unknown symbol|disagrees about version)'; then
        echo "ZFS module compatibility errors in the kernel log" >&2
        exit 1
    fi
fi
echo "PASS: $stage upgrade check, Secure Boot, SELinux and persistent ZFS"
