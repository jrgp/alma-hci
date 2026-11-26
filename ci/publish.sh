#!/usr/bin/env bash
set -euo pipefail

: "${IMAGE_REF:?}" "${LOCAL_IMAGE:?}" "${GITHUB_RUN_ID:?}" "${GITHUB_RUN_ATTEMPT:?}"
: "${COSIGN_PRIVATE_KEY:?}" "${REGISTRY_AUTH_FILE:?}"
: "${GITHUB_SHA:?}" "${GITHUB_REPOSITORY:?}" "${GH_TOKEN:?}"
: "${BASE_IMAGE:?}"

mkdir -p output
cosign public-key --key env://COSIGN_PRIVATE_KEY | cmp - cosign.pub
version="$(sudo podman image inspect "$LOCAL_IMAGE" \
    --format '{{index .Labels "org.opencontainers.image.version"}}')"
[[ "$version" =~ ^[0-9]{8}\.[0-9]+\.[0-9]+$ ]]
release_tag="10-${version}"
base_digest="$(sudo podman image inspect "$LOCAL_IMAGE" \
    --format '{{index .Labels "org.opencontainers.image.base.digest"}}')"
if [[ "$base_digest" != "${BASE_IMAGE##*@}" ]]; then
    echo "The requested upstream base does not match the built image's base digest" >&2
    exit 1
fi
# CI's BASE_IMAGE includes a tag; Skopeo requires a digest-only reference.
base_reference="${BASE_IMAGE%@*}"
base_reference="${base_reference%:*}@${base_digest}"
base_version="$(skopeo inspect "docker://${base_reference}" | jq -er \
    '.Labels["org.opencontainers.image.version"] |
     if type == "string" and length > 0 then .
     else error("The upstream Alma bootc image has no version label") end')"
rpm_format='%{NAME}\t%{ARCH}\t%{EPOCHNUM}:%{VERSION}-%{RELEASE}\n'
sudo podman run --rm "$LOCAL_IMAGE" rpm -qa --qf "$rpm_format" \
    > output/current-packages.tsv
previous_reference=""
previous_options=()
if skopeo inspect "docker://${IMAGE_REF}:10" > output/previous-image.json \
    2> output/previous-image-inspect.log; then
    previous_digest="$(jq -er .Digest output/previous-image.json)"
    previous_reference="${IMAGE_REF}@${previous_digest}"
    cosign verify --insecure-ignore-tlog=true --key cosign.pub \
        "$previous_reference" > /dev/null
    sudo --preserve-env=REGISTRY_AUTH_FILE podman run --rm "$previous_reference" \
        rpm -qa --qf "$rpm_format" > output/previous-packages.tsv
    previous_options=(--previous output/previous-packages.tsv)
elif grep -Fi 'manifest unknown' output/previous-image-inspect.log; then
    echo "No previously published :10 image; this is the first publication."
else
    cat output/previous-image-inspect.log >&2
    echo "Cannot inspect the previous image; refusing to publish an incomplete diff" >&2
    exit 1
fi
candidate="${IMAGE_REF}:build-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
sudo --preserve-env=REGISTRY_AUTH_FILE podman push --format=oci \
    --digestfile output/published-digest "$LOCAL_IMAGE" "docker://${candidate}"
digest="$(cat output/published-digest)"
reference="${IMAGE_REF}@${digest}"

verify_with_host_policy() {
    sudo --preserve-env=REGISTRY_AUTH_FILE podman run --rm \
        --security-opt=label=disable \
        -v "${REGISTRY_AUTH_FILE}:/run/registry-auth.json:ro" \
        -e REGISTRY_AUTH_FILE=/run/registry-auth.json \
        "$LOCAL_IMAGE" skopeo copy "docker://${reference}" dir:/tmp/verified
}

# Stable tags never point at an unsigned or untested candidate.
if verify_with_host_policy > output/unsigned-rejection.log 2>&1; then
    echo "Unsigned candidate was accepted by the image's policy" >&2
    exit 1
fi
if ! grep -F 'A signature was required, but no signature exists' output/unsigned-rejection.log; then
    cat output/unsigned-rejection.log >&2
    echo "Candidate failed for a reason other than the missing signature" >&2
    exit 1
fi

# containers/image currently consumes the legacy .sig attachment, not
# Cosign 3's default bundle format. Keep this compatible with bootc.
cosign sign --yes --tlog-upload=false --key env://COSIGN_PRIVATE_KEY "$reference"
cosign verify --insecure-ignore-tlog=true --key cosign.pub "$reference" \
    > output/signature.json
skopeo inspect --raw "docker://${IMAGE_REF}:${digest/:/-}.sig" > /dev/null
verify_with_host_policy > output/signed-acceptance.log 2>&1

# Exercise the exact signed registry digest before any stable tag moves.
if [[ -n "$previous_reference" ]]; then
    make test-upgrade UPGRADE_FROM_IMAGE="$previous_reference" UPGRADE_TO_IMAGE="$reference"
else
    echo "No previous publication: upgrade test is not applicable to this first release."
fi

jq -n --arg image "$reference" --arg candidate "$candidate" \
    --arg release_tag "$release_tag" --arg previous "$previous_reference" \
    --arg revision "$GITHUB_SHA" --arg base_image "$base_reference" --arg base_version "$base_version" \
    '{image: $image, candidate: $candidate, release_tag: $release_tag,
      previous_image: (if $previous == "" then null else $previous end),
      revision: $revision, base_image: $base_image, base_version: $base_version,
      signatures: "verified", policy: "unsigned rejected; signed accepted"}' \
    > output/published.json
python3 ci/release-notes.py --current output/current-packages.tsv \
    "${previous_options[@]}" --metadata output/published.json \
    --repository "$GITHUB_REPOSITORY" \
    --run-url "https://github.com/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}" \
    --output output

for tag in "$release_tag" 10 latest; do
    skopeo copy --preserve-digests "docker://${reference}" "docker://${IMAGE_REF}:${tag}"
done
gh release create "$release_tag" --repo "$GITHUB_REPOSITORY" \
    --target "$GITHUB_SHA" --title "Alma HCI ${release_tag}" --latest \
    --notes-file output/release-notes.md \
    output/packages.json output/published.json output/signature.json
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    cat output/release-notes.md >> "$GITHUB_STEP_SUMMARY"
fi
