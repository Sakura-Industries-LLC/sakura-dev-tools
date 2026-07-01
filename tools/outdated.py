#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "rich>=13.0.0",
# ]
# ///
"""Check and optionally update tool versions mirrored in .env."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.prompt import Confirm

console = Console()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check proto-managed tools and .env mirrored tool versions.",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help=(
            "run proto outdated --update from tools/stage2, sync .env tool "
            "versions, and prompt for DEBIAN_TAG"
        ),
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    env_path = repo_root / ".env"
    prototools_path = repo_root / ".prototools"
    stage2_dir = repo_root / "tools" / "stage2"
    stage2_prototools_path = stage2_dir / ".prototools"

    if not env_path.exists():
        console.print(f"[red]missing {env_path}[/red]")
        return 1
    if not prototools_path.exists():
        console.print(f"[red]missing {prototools_path}[/red]")
        return 1
    if not stage2_prototools_path.exists():
        console.print(f"[red]missing {stage2_prototools_path}[/red]")
        return 1

    proto_status = run_proto_outdated(update=args.update, cwd=stage2_dir)
    if proto_status != 0:
        return proto_status

    env_values = read_env(env_path)
    prototools_proto = read_prototools_version(prototools_path, "proto")
    if prototools_proto is None:
        console.print("[red]could not read proto version from .prototools[/red]")
        return 1
    stage2_nextest = read_prototools_version(
        stage2_prototools_path,
        "cargo:cargo-nextest",
    )
    if stage2_nextest is None:
        console.print(
            "[red]could not read cargo:cargo-nextest version from "
            "tools/stage2/.prototools[/red]",
        )
        return 1

    sync_env_version(
        env_path=env_path,
        env_values=env_values,
        env_key="PROTO_VERSION",
        source_label=".prototools",
        source_version=prototools_proto,
        update=args.update,
    )
    env_values = read_env(env_path)
    sync_env_version(
        env_path=env_path,
        env_values=env_values,
        env_key="NEXTEST_VERSION",
        source_label="tools/stage2/.prototools",
        source_version=stage2_nextest,
        update=args.update,
    )

    env_values = read_env(env_path)
    current_debian = env_values.get("DEBIAN_TAG")
    if not current_debian:
        console.print("[red]DEBIAN_TAG is not set in .env[/red]")
        return 1

    console.print(f"Current Debian Version == {current_debian}", soft_wrap=True)
    with console.status("checking latest Debian version ...", spinner="dots"):
        latest_debian = latest_debian_slim_tag(current_debian)
    handle_debian_tag(
        env_path=env_path,
        current=current_debian,
        latest=latest_debian,
        update=args.update,
    )
    return 0


def run_proto_outdated(*, update: bool, cwd: Path) -> int:
    """Run proto's own outdated workflow, preserving interactivity."""
    command = ["proto", "outdated"]
    if update:
        command.append("--update")
    return subprocess.run(command, cwd=cwd, check=False).returncode


def read_env(path: Path) -> dict[str, str]:
    """Read simple KEY=VALUE assignments from .env."""
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def write_env_value(path: Path, key: str, value: str) -> None:
    """Set or append one simple KEY=VALUE assignment in .env."""
    lines = path.read_text().splitlines()
    output: list[str] = []
    found = False

    for line in lines:
        if line.startswith(f"{key}="):
            output.append(f"{key}={value}")
            found = True
        else:
            output.append(line)

    if not found:
        output.append(f"{key}={value}")

    path.write_text("\n".join(output) + "\n")


def read_prototools_version(path: Path, key: str) -> str | None:
    """Read one bare or quoted tool version assignment from .prototools."""
    escaped_key = re.escape(key)
    pattern = re.compile(rf'^(?:"{escaped_key}"|{escaped_key})\s*=\s*"([^"]+)"\s*$')
    for line in path.read_text().splitlines():
        match = pattern.match(line)
        if match:
            return match.group(1)
    return None


def sync_env_version(
    *,
    env_path: Path,
    env_values: dict[str, str],
    env_key: str,
    source_label: str,
    source_version: str,
    update: bool,
) -> None:
    """Report or sync one .env version mirror against its source."""
    env_version = env_values.get(env_key)
    if env_version == source_version:
        console.print(f"[green]{env_key} is synced:[/green] {env_version}")
        return

    if update:
        write_env_value(env_path, env_key, source_version)
        console.print(
            f"[green]synced {env_key}:[/green] "
            f"{env_version or '<unset>'} -> {source_version}",
        )
        return

    console.print(f"[yellow]{env_key} differs from {source_label}:[/yellow]")
    console.print(f"  current: {env_version or '<unset>'}")
    console.print(f"  could be: {source_version}")


@dataclass(frozen=True, order=True)
class DebianSlimTag:
    """A numbered Debian slim tag, without any digest suffix."""

    major: int
    minor: int
    tag: str


def latest_debian_slim_tag(current_tag: str) -> DebianSlimTag:
    """Return the latest numbered Docker Hub Debian slim tag."""
    current = parse_debian_slim_tag(strip_digest(current_tag))
    if current is None:
        msg = f"DEBIAN_TAG must be a numbered Debian slim tag, got {current_tag!r}"
        raise RuntimeError(msg)

    url: str | None = (
        "https://registry.hub.docker.com/v2/repositories/library/debian/tags"
        "?page_size=100&name=-slim"
    )
    best: DebianSlimTag | None = None

    while url:
        with urllib.request.urlopen(url, timeout=30) as response:
            payload = json.load(response)

        for result in payload.get("results", []):
            candidate = parse_debian_slim_tag(result.get("name", ""))
            if candidate is None or candidate.major < current.major:
                continue
            if best is None or candidate > best:
                best = candidate

        url = payload.get("next")

    if best is None:
        msg = f"no numbered Debian slim tags found at or after {current.tag!r}"
        raise RuntimeError(msg)
    return best


def strip_digest(tag: str) -> str:
    """Return a Docker tag without an optional @sha256 digest suffix."""
    return tag.split("@", 1)[0]


def parse_debian_slim_tag(tag: str) -> DebianSlimTag | None:
    """Parse numbered Debian tags such as 13.5-slim or 14-slim."""
    match = re.fullmatch(r"(?P<major>\d+)(?:\.(?P<minor>\d+))?-slim", tag)
    if not match:
        return None
    return DebianSlimTag(
        major=int(match.group("major")),
        minor=int(match.group("minor") or 0),
        tag=tag,
    )


def debian_manifest_digest(tag: str) -> str:
    """Resolve the Docker registry digest for a Debian tag."""
    token_url = (
        "https://auth.docker.io/token"
        "?service=registry.docker.io&scope=repository:library/debian:pull"
    )
    with urllib.request.urlopen(token_url, timeout=30) as response:
        token = json.load(response)["token"]

    manifest_url = f"https://registry-1.docker.io/v2/library/debian/manifests/{tag}"
    request = urllib.request.Request(
        manifest_url,
        method="HEAD",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": ", ".join(
                [
                    "application/vnd.oci.image.index.v1+json",
                    "application/vnd.docker.distribution.manifest.list.v2+json",
                    "application/vnd.docker.distribution.manifest.v2+json",
                ],
            ),
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        digest = response.headers.get("Docker-Content-Digest")

    if not digest:
        msg = f"could not resolve digest for debian:{tag}"
        raise RuntimeError(msg)
    return digest


def handle_debian_tag(
    *,
    env_path: Path,
    current: str,
    latest: DebianSlimTag,
    update: bool,
) -> None:
    """Report or optionally update DEBIAN_TAG."""
    current_without_digest = strip_digest(current)
    latest_tag = latest.tag
    current.split("@", 1)[1] if "@" in current else None

    with console.status(f"resolving debian:{latest_tag} digest ...", spinner="dots"):
        latest_pinned = f"{latest_tag}@{debian_manifest_digest(latest_tag)}"

    if current == latest_pinned:
        console.print(
            f"[green]DEBIAN_TAG is current:[/green] {current}", soft_wrap=True
        )
        return

    if current_without_digest == latest_tag:
        console.print(
            "[yellow]DEBIAN_TAG is on the latest tag but is not digest-pinned:[/yellow]",
        )
        console.print(f"  current: {current}")
        console.print(f"  could be: {latest_pinned}", soft_wrap=True)
    else:
        console.print(
            "[yellow]DEBIAN_TAG differs from latest Debian slim tag:[/yellow]"
        )
        console.print(f"  current: {current}")
        console.print(f"  could be: {latest_pinned}", soft_wrap=True)

    if not update:
        return

    if Confirm.ask(
        f"Update DEBIAN_TAG from {current} to {latest_pinned}?",
        default=False,
    ):
        write_env_value(env_path, "DEBIAN_TAG", latest_pinned)
        console.print(
            f"[green]updated DEBIAN_TAG:[/green] {current} -> {latest_pinned}"
        )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
