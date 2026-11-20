#!/usr/bin/env bash
set -euo pipefail

: "${IMAGE_REF:?}" "${LOCAL_IMAGE:?}" "${GITHUB_RUN_ID:?}" "${GITHUB_RUN_ATTEMPT:?}"
: "${COSIGN_PRIVATE_KEY:?}" "${REGISTRY_AUTH_FILE:?}"

mkdir -p output
cosign public-key --key env://COSIGN_PRIVATE_KEY | cmp - cosign.pub
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

for tag in 10 latest; do
    skopeo copy --preserve-digests "docker://${reference}" "docker://${IMAGE_REF}:${tag}"
done
jq -n --arg image "$reference" --arg candidate "$candidate" \
    '{image: $image, candidate: $candidate, signatures: "verified", policy: "unsigned rejected; signed accepted"}' \
    > output/published.json
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    printf '## Published\n\n`%s`\n\nTags: `10`, `latest`, `%s`.\n\nUnsigned rejection and signed acceptance passed using the image policy.\n' \
        "$reference" "${candidate##*:}" >> "$GITHUB_STEP_SUMMARY"
fi
