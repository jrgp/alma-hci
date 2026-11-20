SUDO ?= sudo
PODMAN = $(SUDO) podman
IMAGE_NAME ?= localhost/alma-hci:10
IMAGE_REPOSITORY ?= jrgp/alma-hci
IMAGE_REGISTRY ?= ghcr.io
CONTAINER_FILE ?= ./Dockerfile
INSTALL_TARGET_IMAGE ?= ghcr.io/jrgp/alma-hci:10
ZFS_SIGNING_KEY ?= $(HOME)/.local/share/alma-hci-signing/zfs.key
ZFS_SIGNING_CERT ?= $(CURDIR)/zfs-signing.der

.DEFAULT_GOAL := image
.ONESHELL:
.SHELLFLAGS := -eu -c
.PHONY: image test test-image test-vm

image:
	test -r "$(ZFS_SIGNING_KEY)"
	test -r "$(ZFS_SIGNING_CERT)"
	cert_sha256=$$(sha256sum "$(ZFS_SIGNING_CERT)" | cut -d ' ' -f 1)
	$(PODMAN) build \
		--secret=id=zfs_signing_key,src="$(ZFS_SIGNING_KEY)" \
		--secret=id=zfs_signing_cert,src="$(ZFS_SIGNING_CERT)" \
		--build-arg ZFS_SIGNING_CERT_SHA256="$$cert_sha256" \
		--security-opt=label=disable \
		--cap-add=all \
		--device /dev/fuse \
		--build-arg IMAGE_NAME=$(IMAGE_REPOSITORY) \
		--build-arg IMAGE_REGISTRY=$(IMAGE_REGISTRY) \
		$(if $(BASE_IMAGE),--build-arg BASE_IMAGE=$(BASE_IMAGE)) \
		$(if $(BASE_DIGEST),--build-arg BASE_DIGEST=$(BASE_DIGEST)) \
		$(if $(SOURCE_HASH),--build-arg SOURCE_HASH=$(SOURCE_HASH)) \
		$(if $(BUILD_VERSION),--build-arg BUILD_VERSION=$(BUILD_VERSION)) \
		$(if $(REVISION),--build-arg REVISION=$(REVISION)) \
		-t $(IMAGE_NAME) -f $(CONTAINER_FILE) .

test-image:
	$(PODMAN) run --rm \
		-v "$(CURDIR)/tests/check-image.sh:/check-image.sh:ro,Z" \
		-v "$(ZFS_SIGNING_CERT):/expected-zfs.der:ro,Z" \
		$(IMAGE_NAME) bash /check-image.sh

test-vm:
	$(SUDO) env IMAGE_NAME=$(IMAGE_NAME) \
		INSTALL_TARGET_IMAGE=$(INSTALL_TARGET_IMAGE) python3 tests/test-vm.py

test: test-image test-vm
