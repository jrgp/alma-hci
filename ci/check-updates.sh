#!/usr/bin/env bash
set -euo pipefail

base_tag="${BASE_TAG:-quay.io/almalinuxorg/almalinux-bootc:10}"
image="${IMAGE_REF:-ghcr.io/jrgp/alma-hci}"
base_digest="$(skopeo inspect --format '{{.Digest}}' "docker://${base_tag}")"
base_image="${base_tag}@${base_digest}"
source_hash="$(git ls-tree -r HEAD -- Dockerfile .containerignore files ci tests \
    cosign.pub bootc.pub zfs-signing.der .github/workflows/build.yml | sha256sum | cut -d ' ' -f 1)"

cosign verify --key bootc.pub "$base_image" > /dev/null
build=true
reason="Forced build or first publication"
if [[ "${FORCE:-false}" != true ]]; then
    previous="$(skopeo inspect "docker://${image}:10")"
    previous_digest="$(jq -er .Digest <<< "$previous")"
    cosign verify --insecure-ignore-tlog=true --key cosign.pub \
        "${image}@${previous_digest}" > /dev/null
    if [[ "$(jq -r '.Labels["org.opencontainers.image.base.digest"]' <<< "$previous")" != "$base_digest" ]]; then
        reason="Upstream bootc base changed"
    elif [[ "$(jq -r '.Labels["io.github.alma-hci.source-hash"]' <<< "$previous")" != "$source_hash" ]]; then
        reason="Build inputs or tests changed"
    else
        # Match the build's upgrade policy: the verified base owns the kernel.
        status=0
        sudo --preserve-env=REGISTRY_AUTH_FILE podman run --rm \
            "${image}@${previous_digest}" \
            dnf --refresh --exclude='kernel*' check-update || status=$?
        case "$status" in
            0) build=false; reason="Base, build inputs and installed RPMs are unchanged" ;;
            100) reason="Installed userspace RPM updates are available" ;;
            *) echo "RPM update check failed (exit ${status}); refusing to report no changes" >&2
               exit "$status" ;;
        esac
    fi
fi

printf '%s\n' "$reason"
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    printf '## Change check\n\n%s\n' "$reason" >> "$GITHUB_STEP_SUMMARY"
fi
if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    printf 'build=%s\nbase_image=%s\nbase_digest=%s\nsource_hash=%s\n' \
        "$build" "$base_image" "$base_digest" "$source_hash" >> "$GITHUB_OUTPUT"
else
    printf 'build=%s\nbase_image=%s\nbase_digest=%s\nsource_hash=%s\n' \
        "$build" "$base_image" "$base_digest" "$source_hash"
fi
