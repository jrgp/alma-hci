#!/usr/bin/env bash

set -xeuo pipefail

# Image cleanup
# Specifically called by build.sh

# Image-layer cleanup
shopt -s extglob

dnf clean all

rm -rf /run/selinux-policy /run/mdadm
rm -rf /.gitkeep /var /boot
mkdir -p /boot /var

# Make /usr/local writeable, unless the base image already did it
if [[ ! -L /usr/local ]]; then
    mv /usr/local /var/usrlocal
    ln -s ../var/usrlocal /usr/local
    find /var/usrlocal -type d -printf 'd %p %m root root -\n' \
        > /usr/lib/tmpfiles.d/alma-usrlocal.conf
fi
