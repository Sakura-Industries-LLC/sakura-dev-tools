# sakura-dev-tools

A single, maintainable build-tool container image used across Sakura Industries projects.
The image layers a Debian base, the `proto` toolchain manager,
and a Rust nightly toolchain into one reproducible environment that downstream projects can mount as their build runner.

## Layout

The repository is intentionally small.
Everything that ends up inside the final image lives under `tools/`; everything that drives the build lives at the repo root.

* `tools/Dockerfile.00-base-os` - Debian base layer with native build dependencies.
* `tools/Dockerfile.01-proto` - installs `proto` and every tool pinned in `.prototools`.
* `tools/Dockerfile.02-rust` - adds a nightly Rust toolchain (for `rustfmt`) and `cargo-nextest`.
* `tools/Dockerfile.zz-tools` - the final `sakura-dev-tools:latest` image, with `/repo` as the working directory.
* `tools/outdated.py` - reports which pinned versions in `.env` and `.prototools` have newer upstream releases;
  supports `--update` to refresh them.
* `tools/stage2/` - sidecar pin file for tools that `proto` cannot install safely; see its `README.md`.
* `Justfile` - host entry points for building the image and running the containerized moon tasks.
* `just/local.just` - the same recipes, but executed directly on the host without the container wrapper.
* `moon.yml` - the `fix` and `ci` moon tasks (markdown check/fix, spelling, rustfmt version check).
* `.env` / `.prototools` - pinned versions for `DEBIAN_TAG`, `PROTO_VERSION`, and the rest of the toolchain.

## Layer naming

The Dockerfile prefix is load-bearing.
Each layer names a stage in a fixed order, and the final `zz-tools` image is what consumers mount:

| Prefix | Role                                                                                  |
| ------ | ------------------------------------------------------------------------------------- |
| `00-`  | OS / native build dependencies (Debian).                                               |
| `01-`  | `proto` toolchain manager and everything pinned in `.prototools`.                     |
| `02-`  | Toolchains that `proto` cannot install (currently the nightly Rust toolchain).        |
| `zz-`  | Final aggregated image, with `/repo` prepared for downstream mounts.                  |

`just build-setup` walks the layers in lexicographic order.
New layers go between `02-` and `zz-`; the numeric prefix is the build order, so the final image is always last.

## Building

Install `docker`, `just`, and `moon` on the host.

* `just build-setup` - builds every Dockerfile layer and tags the result as `sakura-dev-tools:latest`.
* `just fix` - runs `moon run :fix` inside the container.
* `just ci` - runs `moon run :ci` inside the container.
* `just fix-ci` - both, in order.
* `just outdated` - reports which pinned versions are behind upstream.
* `just outdated-update` - refreshes the pinned versions.
* `just container-shell` - drops you into a bash shell inside the latest image with the repo mounted at `/repo`.

The local-just recipes (`just local fix`, `just local ci`, `just local fix-ci`) run the moon tasks directly on the host,
bypassing the container.
Use them when iterating on the moon tasks themselves.

## Updating pinned versions

`tools/outdated.py` reads `.env` (for `DEBIAN_TAG`, `PROTO_VERSION`, `NEXTEST_VERSION`) and `.prototools`
(for everything else), queries the upstream registry or release API for each, and prints what is behind.
With `--update`, it rewrites the pin files in place.

## License

Licensed under Apache-2.0.
See `LICENSE`.
