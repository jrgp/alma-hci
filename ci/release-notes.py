#!/usr/bin/env python3
"""Generate release notes and a complete RPM inventory from exact image builds."""

import argparse
import json
from pathlib import Path


def read_packages(path):
    packages = {}
    for line in path.read_text().splitlines():
        name, arch, version = line.split("\t")
        epoch, release = version.split(":", 1)
        if not name or not arch or not epoch.isdigit() or not release:
            raise ValueError(f"Invalid RPM inventory row: {line!r}")
        packages.setdefault((name, arch), set()).add(version)
    if not packages:
        raise ValueError(f"Empty RPM inventory: {path}")
    return packages


def display_versions(versions):
    return ", ".join(f"`{version.removeprefix('0:')}`" for version in sorted(versions)) or "-"


def package_diff(previous, current):
    rows = []
    for name, arch in sorted(previous.keys() | current.keys()):
        key = (name, arch)
        old, new = previous.get(key, set()), current.get(key, set())
        if old != new:
            rows.append(
                f"| {name}.{arch} | {display_versions(old)} | {display_versions(new)} |"
            )
    if not rows:
        return "No RPM changes detected.\n"
    return "| Package | Previous | New |\n| --- | --- | --- |\n" + "\n".join(rows) + "\n"


def release_notes(metadata, previous, current, repository, run_url):
    tag = metadata["release_tag"]
    image = metadata["image"]
    image_repo = image.split("@", 1)[0]
    revision = metadata["revision"]
    baseline = metadata["previous_image"]
    comparison = (
        f"Compared against the previously published image:\n\n`{baseline}`."
        if baseline else "First publication; all installed RPMs are listed as additions."
    )
    return (
        f"# Alma HCI {tag}\n\n"
        f"| Build | Value |\n| --- | --- |\n"
        f"| Dated image | `{image_repo}:{tag}` |\n"
        f"| Immutable image | `{image}` |\n"
        f"| Source | [{revision[:7]}](https://github.com/{repository}/commit/{revision}) |\n"
        f"| Workflow | [Build and verification]({run_url}) |\n\n"
        f"## Package changes\n\n{comparison}\n\n"
        f"{package_diff(previous, current)}\n"
        "Packages are compared by name and architecture, including RPM epochs and "
        "multiple installed versions. `-` indicates an added or removed package. "
        "The complete inventory is attached as `packages.json`.\n\n"
        "## Update\n\n"
        "`:10` and `:latest` point to this tested, signed image.\n\n"
        "```sh\nsudo bootc upgrade\nsudo systemctl reboot\n```\n\n"
        "To select this exact dated build:\n\n"
        f"```sh\nsudo bootc switch --enforce-container-sigpolicy {image_repo}:{tag}\n"
        "sudo systemctl reboot\n```\n"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    current = read_packages(args.current)
    previous = read_packages(args.previous) if args.previous else {}
    metadata = json.loads(args.metadata.read_text())
    if bool(metadata["previous_image"]) != bool(args.previous):
        raise ValueError("Previous image metadata and RPM inventory must agree")
    inventory = [
        {"name": name, "arch": arch, "versions": sorted(versions)}
        for (name, arch), versions in sorted(current.items())
    ]
    (args.output / "packages.json").write_text(json.dumps(inventory, indent=2) + "\n")
    (args.output / "release-notes.md").write_text(
        release_notes(metadata, previous, current, args.repository, args.run_url)
    )


if __name__ == "__main__":
    main()
