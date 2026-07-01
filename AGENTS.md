# AGENTS - Guidelines for Automated Assistants

This file provides repo-wide guidance for automated coding assistants working in `sakura-dev-tools`.
It applies to the entire repository unless a more specific `AGENTS.md` exists in a subdirectory.

## What this repo is

`sakura-dev-tools` is a small, opinionated build-tool container image.
The whole repository exists to produce one Docker image (`sakura-dev-tools:latest`) and to drive it from the host.
There is no application code, no language workspace, and no specs.

## General principles

* Make **small, targeted changes** that directly address the user's request.
* Prefer **clarity and correctness** over cleverness; do not refactor broadly unless explicitly asked.
* Keep the existing structure (Dockerfile layering, Justfile layout, moon tasks) intact
  unless there is a strong reason to change it.
* When in doubt, **ask the user** rather than guessing.

## Dockerfile layering

The image is built in named stages with a numeric prefix that controls build order.

* `00-` - OS / native build dependencies (Debian).
* `01-` - `proto` toolchain manager and every pin in `.prototools`.
* `02-` - toolchains that `proto` cannot install (currently the nightly Rust toolchain).
* `zz-` - the final aggregated image; this is what downstream projects mount.

`just build-setup` walks the layers in lexicographic order.
Add new layers between `02-` and `zz-`, never at the end.
Each Dockerfile should `FROM` the previous stage so the build cache is shared.

## Pinned versions

Two files hold the pin set, and they are the single source of truth.

* `.env` - `DEBIAN_TAG`, `PROTO_VERSION`, `NEXTEST_VERSION` (consumed by `just build-setup` as `--build-arg`).
* `.prototools` - every other tool the image provides.

Use `just outdated` to see what is behind, and `just outdated-update` to refresh.
Do not hand-edit a pin to a version that `outdated.py` does not know how to resolve.

## Justfile and moon tasks

* `Justfile` is the host entry point; `just/local.just` exposes the same recipes without the container wrapper.
* `moon.yml` defines `fix` (markdown auto-fix plus a nightly `rustfmt --version` smoke check) and `ci` (markdown check plus cspell).
* Markdown style is enforced by `rumdl` (see `.rumdl.toml`); spelling by `cspell` (see `cspell.json`).

When validating changes, prefer:

* `just fix-ci` for the full check set.

Do NOT run `moon` directly, Do Not use any `just local` targets as they are exclusively developer conveniences.

## `tools/outdated.py`

A small Python utility that reports stale pins in `.env`, `.prototools`, and the sidecar pin file under `tools/stage2/`.
It is the only place that knows how to translate a "latest" release back into a pin entry; do not duplicate that logic elsewhere.

## Documentation

* Follow the Markdown style in `.rumdl.toml`; in particular, keep the "one sentence per line" convention where it is used.
* When adding terminology that is likely to trigger the spell checker, add it to `.config/dictionaries/project.dic` in sorted order.

## Things to avoid

* Do not change `LICENSE`, `CODE_OF_CONDUCT.md`, or security policies.
* Do not mass-reformat the repo; limit formatting to files you touch.
* Do not introduce a new Dockerfile layer prefix without discussing it first; the prefix is part of the build contract.
* Do not move or rename the moon tasks without confirming the downstream projects still match.
