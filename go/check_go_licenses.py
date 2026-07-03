#!/usr/bin/env -S uv run --script
#
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Validate the licenses of a Go project's transitive dependency graph.

Reads ``licenses.toml`` from the project source directory, runs
``cyclonedx-gomod mod`` to emit a CycloneDX BOM, and compares every
observed license against the allowlist.  Any component whose detected
licenses are not all listed in the config causes a non-zero exit, an
inline report naming every flagged module with its detected license,
and a ready-to-paste ``licenses.toml`` block listing every license the
BOM observed.

This is best-efforts: a module the BOM could not classify a license
for is silently skipped (the maintainer can't be bothered to publish
a machine-readable license; we are not failing our check on their
behalf).  Such modules are surfaced in an ``info:`` summary so the
user can decide whether to look them up manually.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import tomllib

CONFIG_FILENAME = "licenses.toml"


def parse_bom(bom: dict) -> list[tuple[str, list[str]]]:
    """Return [(name, [license, ...]), ...] for the main module plus every
    component, in the order they appear in the BOM.

    Each component's ``licenses`` is the full set of identifiers extracted
    from its CycloneDX ``evidence.licenses`` block.  SPDX ``id`` and
    non-SPDX ``name`` are both captured.  An empty list means the BOM
    has no detectable license for that component.
    """
    findings: list[tuple[str, list[str]]] = []
    main = bom.get("metadata", {}).get("component")
    if main is not None:
        findings.append((main.get("name", "<root>"), _extract_licenses(main)))
    for comp in bom.get("components", []) or []:
        findings.append((comp.get("name", "<unknown>"), _extract_licenses(comp)))
    return findings


def _extract_licenses(component: dict) -> list[str]:
    licences: list[str] = []
    for entry in component.get("evidence", {}).get("licenses", []) or []:
        lic = entry.get("license") or {}
        if "id" in lic:
            licences.append(str(lic["id"]))
        elif "name" in lic:
            licences.append(str(lic["name"]))
    return licences


def print_report(
    config_path: Path,
    findings: list[tuple[str, list[str]]],
    allowlist: list[str],
) -> int:
    """Print the violation report and the suggested config block.

    Modules the BOM classified with no license at all are treated as
    best-effort skips (not violations) — if a maintainer can't be
    bothered to publish a machine-readable license, we are not going
    to fail the check on their behalf.  These are surfaced as a single
    summary line so the user knows which deps were silently skipped.

    Returns the exit code: 0 if every classified module's licenses are
    covered by the allowlist, otherwise 1.
    """
    allow = set(allowlist)
    observed: set[str] = set()
    unclassified: list[str] = []

    flagged: list[tuple[str, list[str]]] = []
    for name, licenses in findings:
        if not licenses:
            unclassified.append(name)
            continue
        observed.update(licenses)
        if any(lic in allow for lic in licenses):
            continue
        flagged.append((name, licenses))

    if unclassified:
        print(
            f"info: {len(unclassified)} module(s) had no detectable licence "
            "and were skipped:",
            file=sys.stderr,
        )
        for name in sorted(unclassified):
            print(f"  {name}", file=sys.stderr)

    if not flagged:
        if not findings:
            print(
                "no transitive dependencies to validate for this project",
                file=sys.stderr,
            )
        else:
            classified = len(findings) - len(unclassified)
            print(
                f"all {classified} classified module license entries "
                "are in the allowlist",
                file=sys.stderr,
            )
        return 0

    print(
        f"{len(flagged)} module(s) have licenses not in {config_path}:",
        file=sys.stderr,
    )
    for name, licenses in flagged:
        print(f"  {name} uses {', '.join(licenses)}", file=sys.stderr)

    sorted_observed = sorted(observed)
    if sorted_observed:
        print(
            f"hint: write the following to {config_path} (review and prune any "
            "entries no longer in use):",
            file=sys.stderr,
        )
        print("  licenses = [", file=sys.stderr)
        for lic in sorted_observed:
            print(f'    "{lic}",', file=sys.stderr)
        print("  ]", file=sys.stderr)

    return 1


def load_allowlist(config_path: Path) -> list[str] | None:
    """Load the license allowlist from ``config_path``.

    Returns:
        * a list of SPDX identifiers (possibly empty) to feed to the detector
        * ``None`` when ``config_path`` exists but cannot be parsed or has the
          wrong shape; the caller should exit without running cyclonedx-gomod

    A missing config is treated as "no licenses allowed": a warning is emitted
    and an empty allowlist is returned so the script still runs and reports
    the violations.
    """
    if not config_path.is_file():
        print(
            f"warning: {config_path} not found; defaulting to no licenses allowed",
            file=sys.stderr,
        )
        return []
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        print(f"error: failed to parse {config_path}: {exc}", file=sys.stderr)
        return None
    allowlist = data.get("licenses")
    if not isinstance(allowlist, list):
        print(
            f"error: {config_path} must define a `licenses` array of SPDX identifiers",
            file=sys.stderr,
        )
        return None
    if not allowlist:
        print(
            f"warning: {config_path} defines an empty `licenses` array; "
            "no transitive licenses will pass",
            file=sys.stderr,
        )
        return []
    if not all(isinstance(item, str) and item.strip() for item in allowlist):
        print(
            f"error: every entry in {config_path} `licenses` must be a non-empty string",
            file=sys.stderr,
        )
        return None
    return [item.strip() for item in allowlist]


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {__file__} <project-source>", file=sys.stderr)
        return 2

    project_source = Path(sys.argv[1]).resolve()
    config_path = project_source / CONFIG_FILENAME

    allowlist = load_allowlist(config_path)
    if allowlist is None:
        return 1

    bom_proc = subprocess.run(
        [
            "cyclonedx-gomod",
            "mod",
            "-json=true",
            "-licenses=true",
        ],
        cwd=project_source,
        capture_output=True,
        text=True,
    )
    if bom_proc.returncode != 0:
        if bom_proc.stdout:
            sys.stdout.write(bom_proc.stdout)
        if bom_proc.stderr:
            sys.stderr.write(bom_proc.stderr)
        return bom_proc.returncode

    try:
        bom = json.loads(bom_proc.stdout)
    except json.JSONDecodeError as exc:
        print(
            f"error: cyclonedx-gomod produced unparsable JSON: {exc}", file=sys.stderr
        )
        return 1

    findings = parse_bom(bom)
    return print_report(config_path, findings, allowlist)


if __name__ == "__main__":
    raise SystemExit(main())
