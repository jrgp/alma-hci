#!/usr/bin/env bash
set -euxo pipefail

stage="${1:?expected untrusted, first or reboot}"
[[ "$stage" == untrusted || "$stage" == first || "$stage" == reboot ]]
[[ "$(systemctl get-default)" == multi-user.target ]]
[[ "$(getenforce)" == Enforcing ]]
mokutil --sb-state | grep -x 'SecureBoot enabled'
grep -E '\[(integrity|confidentiality)\]' /sys/kernel/security/lockdown
if [[ "$stage" == untrusted ]]; then
    if mokutil --test-key /usr/share/alma-hci/zfs.der; then
        echo "The negative test already trusts the ZFS certificate" >&2
        exit 1
    fi
    test ! -d /sys/module/zfs
    if modprobe zfs > /var/tmp/zfs-rejection.log 2>&1; then
        echo "Secure Boot accepted a module without the enrolled key" >&2
        exit 1
    fi
    cat /var/tmp/zfs-rejection.log
    grep -Ei 'Key was rejected by service|Required key not available' /var/tmp/zfs-rejection.log
    echo "PASS: Secure Boot boots Alma but rejects ZFS before enrollment"
    exit 0
fi
mokutil --test-key /usr/share/alma-hci/zfs.der
systemctl is-active NetworkManager.service sshd.service
ip -brief address
brctl show
nmcli general status
nmstatectl show > /var/tmp/nmstate.yml
if touch /usr/bootc-write-must-fail; then
    rm /usr/bootc-write-must-fail
    echo "/usr is unexpectedly writable" >&2
    exit 1
fi
bootc status --json | tee /var/tmp/bootc-status.json
jq -e '.status.booted.image.image.image != null' /var/tmp/bootc-status.json
rpm -q kmod-zfs zfs libvirt-daemon-kvm qemu-kvm
if rpm -qa --qf '%{NAME}\n' | grep -Ex 'dkms|zfs-dkms'; then
    echo "DKMS must not be installed" >&2
    exit 1
fi

# Do not modprobe ZFS here: this checks automatic loading on both boots.
test -d /sys/module/zfs
test -c /dev/zfs
zfs version
systemctl is-active zfs-zed.service virtqemud.service virtnetworkd.service
test -c /dev/kvm
virsh -c qemu:///system list --all
virsh -c qemu:///system capabilities > /var/tmp/libvirt-capabilities.xml
grep -q "domain type='kvm'" /var/tmp/libvirt-capabilities.xml
virsh -c qemu:///system net-info default | grep -E 'Active:.*yes'

if [[ "$stage" == first ]]; then
    # Only the dedicated, labelled virtio test disk is ever handed to zpool.
    test_disk=/dev/disk/by-id/virtio-alma-zfs-test
    test -b "$test_disk"
    test "$(lsblk -dnro TYPE "$test_disk")" = disk
    if findmnt --source "$test_disk"; then
        echo "Refusing to use a mounted test disk" >&2
        exit 1
    fi
    zpool create -o ashift=12 -o cachefile=/etc/zfs/zpool.cache \
        -O mountpoint=/var/lib/alma-zfs-test -O compression=lz4 \
        alma-test "$test_disk"
    zfs create alma-test/data
    dd if=/dev/urandom of=/var/lib/alma-zfs-test/data/payload bs=1M count=32 conv=fsync
    sha256sum /var/lib/alma-zfs-test/data/payload > /var/tmp/zfs-payload.sha256
    zfs snapshot alma-test/data@before-reboot
    zfs clone alma-test/data@before-reboot alma-test/clone
    cmp /var/lib/alma-zfs-test/data/payload /var/lib/alma-zfs-test/clone/payload
    zfs destroy alma-test/clone
    zpool export alma-test
    zpool import -d /dev alma-test
    sha256sum -c /var/tmp/zfs-payload.sha256
    zpool set cachefile=/etc/zfs/zpool.cache alma-test

    cat > /var/tmp/nested-smoke.xml <<'EOF'
<domain type='kvm'>
  <name>alma-nested-smoke</name>
  <memory unit='MiB'>128</memory>
  <vcpu>1</vcpu>
  <os><type arch='x86_64' machine='q35'>hvm</type></os>
  <cpu mode='host-passthrough'/>
  <devices>
    <emulator>/usr/libexec/qemu-kvm</emulator>
    <serial type='pty'><target port='0'/></serial>
    <console type='pty'><target type='serial' port='0'/></console>
  </devices>
</domain>
EOF
    virsh -c qemu:///system create /var/tmp/nested-smoke.xml
    virsh -c qemu:///system domstate alma-nested-smoke | grep -x running
    virsh -c qemu:///system qemu-monitor-command alma-nested-smoke \
        '{"execute":"query-kvm"}' | jq -e '.return.enabled and .return.present'
    virsh -c qemu:///system destroy alma-nested-smoke
else
    # The normal ZFS services must import and mount this without test assistance.
    systemctl is-active zfs-import-cache.service zfs-mount.service
    zpool list -H -o health alma-test | grep -x ONLINE
    [[ "$(findmnt -no FSTYPE /var/lib/alma-zfs-test/data)" == zfs ]]
    sha256sum -c /var/tmp/zfs-payload.sha256
    zfs list -t snapshot alma-test/data@before-reboot
    zpool scrub alma-test
    zpool wait -t scrub alma-test
    zpool status -x alma-test | grep 'is healthy'
    if journalctl -k -b --no-pager | grep -Ei '(zfs|spl):.*(unknown symbol|disagrees about version)'; then
        echo "ZFS module compatibility errors in the kernel log" >&2
        exit 1
    fi
fi
echo "PASS: $stage boot, SELinux, bootc, ZFS and libvirt/KVM"
