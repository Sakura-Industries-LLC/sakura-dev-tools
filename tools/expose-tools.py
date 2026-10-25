#!/usr/bin/env python3
"""Expose installed distributions through ordinary filesystem links on PATH."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import tomllib


def mise(*arguments: str) -> str:
    return subprocess.check_output(["mise", *arguments], text=True).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, default=Path("/opt/toolchain"))
    prefix = parser.parse_args().prefix
    binaries = prefix / "bin"
    binaries.mkdir(parents=True, exist_ok=True)
    config = tomllib.loads(Path("mise.toml").read_text())
    installed = json.loads(mise("ls", "--current", "--json"))
    for tool, pin in config["tools"].items():
        expected = pin["version"] if isinstance(pin, dict) else pin
        records = installed.get(tool, [])
        # Go module pins may include a v prefix that mise removes when installing.
        if not any(
            record["installed"] and record["version"].removeprefix("v") == expected.removeprefix("v")
            for record in records
        ):
            raise RuntimeError(f"Expected exact installed version {tool}@{expected}")
    data = Path(os.environ["MISE_DATA_DIR"]).resolve()
    environment = json.loads(mise("env", "--json"))
    # Keep mise's PATH precedence for the installed tool distributions.
    for directory in environment["PATH"].split(os.pathsep):
        source = Path(directory)
        if not source.is_dir() or not source.resolve().is_relative_to(data):
            continue
        for executable in sorted(source.iterdir()):
            if not executable.is_file() or not os.access(executable, os.X_OK):
                continue
            destination = binaries / executable.name
            if not destination.is_symlink() and not destination.exists():
                destination.symlink_to(executable)

    # Bypass Node's mise-generated npm wrapper and expose the pinned npm package.
    npm = Path(mise("where", "npm:npm")) / "node_modules/npm/bin"
    for name in ("npm", "npx"):
        destination = binaries / name
        destination.unlink(missing_ok=True)
        destination.symlink_to(npm / f"{name}-cli.js")

    for name, tool in (("java", "java"), ("go", "go")):
        root = Path(mise("where", tool))
        if not root.is_dir():
            raise RuntimeError(f"Missing installation for {tool}: {root}")
        (prefix / name).symlink_to(root)
    (prefix / "gradle").symlink_to((binaries / "gradle").resolve().parent.parent)
    for name in ("java", "javac", "gradle", "node", "npm", "go", "python3", "moon", "just"):
        if not os.access(binaries / name, os.X_OK):
            raise RuntimeError(f"Missing executable after installation: {name}")


if __name__ == "__main__":
    main()
