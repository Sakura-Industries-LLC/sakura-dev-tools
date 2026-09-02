# sakura-dev-tools

A single, maintainable build-tool container image used across Sakura Industries projects.
The image layers a Debian base, the `proto` toolchain manager, a Rust nightly toolchain, and a Go tooling layer
(with the `check_go_licenses.py` license validator)
into one reproducible environment that downstream projects can mount as their build runner.

## Layout

The repository is intentionally small.
Everything that ends up inside the final image lives under `tools/`; everything that drives the build lives at the repo root.

* `tools/Containerfile.00-base-os` - Debian base layer with native build dependencies.
* `tools/Containerfile.01-proto` - installs `proto` and every tool pinned in `.prototools`.
* `tools/Containerfile.02-rust` - adds a nightly Rust toolchain (for `rustfmt`) and `cargo-nextest`.
* `tools/Containerfile.03-go` - Go tooling layer:
  copies `go/check_go_licenses.py` into `/root/go` so it is on `PATH` for downstream Go projects.
  `cyclonedx-gomod` itself is installed at the proto layer via `.prototools`.
* `tools/Containerfile.03a-git-builder` - builds the pinned Git release with its build dependencies.
* `tools/Containerfile.04-pdf2htmlex` - copies the pdf2htmlEX runtime artifacts without build dependencies.
* `tools/Containerfile.05-git` - copies the installed Git runtime from the disposable Git builder.
* `tools/Containerfile.zz-tools` - the final `sakura-dev-tools:latest` image, with `/repo` as the working directory.
* `tools/outdated.py` - reports which pinned versions in `.env` and `.prototools` have newer upstream releases;
  supports `--update` to refresh them.
* `tools/stage2/` - sidecar pin file for tools that `proto` cannot install safely; see its `README.md`.
* `Justfile` - host entry points for building the image and running the containerized moon tasks.
* `just/local.just` - the same recipes, but executed directly on the host without the container wrapper.
* `moon.yml` - the `fix` and `ci` moon tasks (markdown check/fix, spelling, rustfmt version check).
* `.env` / `.prototools` - pinned versions for `DEBIAN_TAG`, `PROTO_VERSION`, and the rest of the toolchain.

## Layer naming

The Containerfile prefix is load-bearing.
Each layer names a stage in a fixed order, and the final `zz-tools` image is what consumers mount:

| Prefix | Role                                                                                  |
| ------ | ------------------------------------------------------------------------------------- |
| `00-`  | OS / native build dependencies (Debian).                                               |
| `01-`  | `proto` toolchain manager and everything pinned in `.prototools`.                     |
| `02-`  | Toolchains that `proto` cannot install (currently the nightly Rust toolchain).        |
| `03-`  | Go tooling layer: ships `check_go_licenses.py`.                                       |
| `03a-` | Disposable Git builder layer.                                                        |
| `04-`  | pdf2htmlEX runtime layer.                                                           |
| `05-`  | Clean Git runtime layer.                                                            |
| `zz-`  | Final aggregated image, with `/repo` prepared for downstream mounts.                  |

`just build-setup` walks the layers in lexicographic order.
New layers go between `02-` and `zz-`; the numeric prefix is the build order, so the final image is always last.

## Building

Install `podman`, `just`, and `moon` on the host.

* `just build-setup` - builds every Containerfile layer for the native platform and tags the result as `sakura-dev-tools:latest`.
* `just build-setup --all` - builds `linux/amd64` and `linux/arm64`,
  then tags the combined multi-architecture manifest as `sakura-dev-tools:latest`.
  Cross-platform `RUN` instructions need an ARM64 binfmt/QEMU handler on the host.
  On a trusted build host, register it with `sudo podman run --privileged --rm docker.io/tonistiigi/binfmt --install arm64`.
* `just fix` - runs `moon run :fix` inside the container.
* `just ci` - runs `moon run :ci` inside the container.
* `just fix-ci` - both, in order.
* `just outdated` - reports which pinned versions are behind upstream.
* `just outdated-update` - refreshes the pinned versions.
* `just container-shell` - drops you into a bash shell inside the latest image with the repo mounted at `/repo`.

The local-just recipes (`just local fix`, `just local ci`, `just local fix-ci`) run the moon tasks directly on the host,
bypassing the container.
Use them when iterating on the moon tasks themselves.

## Publishing

Woodpecker publishes releases for tags matching `v*`.
The release workflow builds `linux/amd64` and `linux/arm64` through `just build-setup --all`,
then pushes a multi-architecture manifest to both the version tag and `latest` at `ghcr.io/sakura-industries-llc/sakura-dev-tools`.

The Woodpecker repository needs Trusted Security enabled for the privileged Podman build step.
It also needs `ghcr_username` and `ghcr_token` secrets,
where the token is a GitHub classic personal access token with `write:packages` permission.
The workflow registers the ARM64 binfmt handler before it builds the image.

## Updating pinned versions

`tools/outdated.py` reads `.env` (for `DEBIAN_TAG`, `PROTO_VERSION`, `NEXTEST_VERSION`, and `GIT_VERSION`) and `.prototools`
(for everything else), queries the upstream registry or release API for each, and prints what is behind.
With `--update`, it rewrites the pin files in place.

## License

Licensed under Apache-2.0.
See `LICENSE`.
