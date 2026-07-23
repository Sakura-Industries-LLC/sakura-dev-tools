#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "rich>=13.0.0",
# ]
# ///
"""Report the sizes of the sakura-dev-tools image layers."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass

from rich.console import Console
from rich.table import Table


IMAGE_PREFIX = "sakura-dev-tools:"
LAYER_PATTERN = re.compile(r"^(?P<name>\d{2}-[^:]+)$")


@dataclass(frozen=True)
class ImageSize:
    name: str
    size: int


def main() -> int:
    images = find_images()
    if not images:
        Console().print("[yellow]No sakura-dev-tools images found.[/yellow]")
        return 0

    table = Table(title="Sakura Dev Tools Image Sizes")
    table.add_column("Image", style="cyan")
    table.add_column("Added Size", justify="right", style="green")
    table.add_column("Image Size", justify="right")
    table.add_column("Included", justify="center")

    previous_any_size = 0
    previous_non_builder_size = 0
    for image in images:
        is_builder = image.name.endswith("-builder")
        parent_size = previous_any_size if is_builder else previous_non_builder_size
        included = "no" if is_builder else "yes"
        table.add_row(
            f"{IMAGE_PREFIX}{image.name}",
            format_size(image.size - parent_size),
            format_size(image.size),
            included,
        )
        previous_any_size = image.size
        if not is_builder:
            previous_non_builder_size = image.size

    latest = find_latest()
    if latest is not None:
        parent_size = previous_any_size
        table.add_row(
            "sakura-dev-tools:latest",
            format_size(latest - parent_size),
            format_size(latest),
            "final",
        )

    table.add_section()
    if latest is not None:
        table.add_row(
            "Cumulative final image",
            "",
            format_size(latest),
            "",
        )
    Console().print(table)
    return 0


def find_images() -> list[ImageSize]:
    images: list[ImageSize] = []
    for reference in podman_references():
        if not reference.startswith(IMAGE_PREFIX):
            continue
        name = reference.removeprefix(IMAGE_PREFIX)
        if LAYER_PATTERN.fullmatch(name) is None:
            continue
        images.append(ImageSize(name=name, size=inspect_size(reference)))
    return sorted(images, key=lambda image: int(image.name[:2]))


def find_latest() -> int | None:
    reference = f"{IMAGE_PREFIX}latest"
    return inspect_size(reference) if reference in podman_references() else None


def podman_references() -> list[str]:
    result = subprocess.run(
        ["podman", "image", "ls", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
    )
    references: list[str] = []
    for record in json.loads(result.stdout):
        for name in record.get("Names", []):
            references.append(name.rsplit("/", 1)[-1])
    return references


def inspect_size(reference: str) -> int:
    result = subprocess.run(
        ["podman", "image", "inspect", "--format", "json", reference],
        check=True,
        capture_output=True,
        text=True,
    )
    return int(json.loads(result.stdout)[0]["Size"])


def format_size(size: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    value = float(size)
    for unit in units:
        if abs(value) < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
