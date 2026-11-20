ARG BASE_IMAGE=quay.io/almalinuxorg/almalinux-bootc:10@sha256:8b7143682e8b911029dd7ad8951d5e8f5ed0b40f1e5f4b76608e9d38ce130e7b

# Allow build scripts to be referenced without being copied into the final image
FROM scratch AS ctx

COPY files/system /system_files/
COPY --chmod=0755 files/scripts /build_files/
COPY *.pub /keys/

FROM ${BASE_IMAGE} AS zfs-kmod
ARG ZFS_SIGNING_CERT_SHA256
COPY ci/build-zfs-kmod.sh /build-zfs-kmod.sh
RUN --mount=type=secret,id=zfs_signing_key \
    --mount=type=secret,id=zfs_signing_cert \
    bash /build-zfs-kmod.sh

# Base Image
FROM ${BASE_IMAGE}

ARG IMAGE_NAME=jrgp/alma-hci
ARG IMAGE_REGISTRY=ghcr.io
ARG VARIANT
ARG BASE_DIGEST
ARG SOURCE_HASH=local
ARG BUILD_VERSION=local
ARG REVISION=local

LABEL org.opencontainers.image.title="Alma HCI" \
      org.opencontainers.image.description="Headless AlmaLinux bootc with libvirt/KVM and OpenZFS kmods" \
      org.opencontainers.image.source="https://github.com/jrgp/alma-hci" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.version="${BUILD_VERSION}" \
      org.opencontainers.image.base.digest="${BASE_DIGEST}" \
      io.github.alma-hci.source-hash="${SOURCE_HASH}"

RUN --mount=type=tmpfs,dst=/opt \
    --mount=type=tmpfs,dst=/tmp \
    --mount=type=bind,from=ctx,source=/,target=/ctx \
    --mount=type=bind,from=zfs-kmod,source=/rpms,target=/zfs-rpms \
    /ctx/build_files/build.sh

### LINTING
## Verify final image and contents are correct.
RUN bootc container lint
