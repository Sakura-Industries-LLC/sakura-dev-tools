# sakura-dev-tools

A single, maintainable build-tool container image used across Sakura Industries projects.
The image layers a Debian base, the `mise` toolchain manager, a Rust nightly toolchain, and a Go tooling layer
(with the `check_go_licenses.py` license validator)
into one reproducible environment that downstream projects can mount as their build runner.

## Layout

The repository is intentionally small.
Everything that ends up inside the final image lives under `tools/`; everything that drives the build lives at the repo root.

* `tools/Containerfile.00-base-os` - Debian base layer with native build dependencies.
* `tools/Containerfile.01-mise` - installs the pinned mise release and every tool pinned in `mise.toml`.
* `tools/Containerfile.01a-pdf2htmlex-builder` - builds the pdf2htmlEX runtime artifacts from the base OS.
* `tools/Containerfile.02-rust` - adds a nightly Rust toolchain (for `rustfmt`) and `cargo-nextest`.
* `tools/Containerfile.02a-git-builder` - builds the pinned Git release from the Rust layer, in parallel with Go and Android.
* `tools/Containerfile.03-go` - Go tooling layer:
  copies `go/check_go_licenses.py` into `/root/go` so it is on `PATH` for downstream Go projects.
  `cyclonedx-gomod` itself is installed at the mise layer via `mise.toml`.
* `tools/Containerfile.04-android` - installs the Android SDK, NDK, emulator, and phone/tablet virtual devices on AMD64.
* `tools/Containerfile.05-pdf2htmlex` - copies the pdf2htmlEX runtime artifacts without build dependencies.
* `tools/Containerfile.06-git` - copies the installed Git runtime from the disposable Git builder.
* `tools/Containerfile.zz-tools` - the final layered `sakura-dev-tools:latest-all` image, with `/repo` as the working directory.
* `tools/outdated.py` - reports which pinned versions in `.env` and `mise.toml` have newer upstream releases;
  supports `--update` to refresh them.
* `Justfile` - host entry points for building the image and running the containerized moon tasks.
* `just/local.just` - the same recipes, but executed directly on the host without the container wrapper.
* `moon.yml` - the `fix` and `ci` moon tasks (markdown check/fix, spelling, rustfmt version check).
* `.env` / `mise.toml` - exact pins for the image layers, mise, and the toolchain.

## Layer naming

The Containerfile prefix is load-bearing.
Each layer names a stage in a fixed order, and the final `zz-tools` image is what consumers mount:

| Prefix | Role                                                                                  |
| ------ | ------------------------------------------------------------------------------------- |
| `00-`  | OS / native build dependencies (Debian).                                               |
| `01-`  | `mise` toolchain manager and everything pinned in `mise.toml`.                     |
| `01a-` | Disposable pdf2htmlEX builder layer.                                                |
| `02-`  | Dated nightly Rust toolchain and cargo-nextest.        |
| `02a-` | Disposable Git builder layer.                                                        |
| `03-`  | Go tooling layer: ships `check_go_licenses.py`.                                       |
| `04-`  | Android SDK, NDK, and emulator layer (AMD64).                                         |
| `05-`  | pdf2htmlEX runtime layer.                                                           |
| `06-`  | Clean Git runtime layer.                                                            |
| `zz-`  | Final aggregated image, with `/repo` prepared for downstream mounts.                  |

`just build-setup` starts the pdf2htmlEX builder after the base OS, in parallel with the main toolchain layers.
It starts the Git builder after Rust is installed, in parallel with Go and Android.
After Go and Android are installed, it waits for each builder before copying its runtime artifacts into the image.
New layers go between `02-` and `zz-`; the numeric prefix is the build order, so the final image is always last.

## Building

Install `podman`, `just`, and `moon` on the host.

* `just build-setup` - builds every Containerfile layer for the native platform as `sakura-dev-tools:latest-all`,
  then squashes all layers into the single-layer `sakura-dev-tools:latest` image while preserving image settings.
  The size report includes the build stages and both final images.
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

## Java and Android

Java 21 (Eclipse Temurin) and Gradle are installed through `mise.toml` on both image architectures.
Every tool has a definite release version; the image does not use wildcard or latest version requests, or a mise lockfile.
`just outdated-update` refreshes the exact pins, keeping Java on Temurin 21.
The image build explicitly trusts `/toolchain/mise.toml` before installing its tools.

Mise retains complete distributions under `/opt/mise`.
The build exposes their executables through ordinary links in `/opt/toolchain/bin`, which is on the static image `PATH`.
Commands run directly without mise shims, shell activation, or `mise exec`, including in noninteractive containers.
`JAVA_HOME`, `GRADLE_HOME`, and `GOROOT` point to stable distribution links under `/opt/toolchain`.
Npm global installs use `/usr/local`, and Go's `GOBIN` is `/usr/local/bin`; Cargo retains `/root/.cargo` and `/root/.rustup`.
Gradle projects can continue to use their own Gradle wrapper.

The Rust layer installs the exact `RUST_NIGHTLY_VERSION` from `.env` and exposes it through the existing `+nightly` name.
The Git builder starts after that layer, in parallel with Go and Android.

The `linux/amd64` image also includes the Android command line tools, platform tools, SDK platform, build tools, NDK, emulator,
and a Google APIs system image.
Their pins live in `.env` and are resolved by `tools/outdated.py` from Google's stable package metadata.
SDK and system image package revisions can change upstream even when their package identifiers stay the same.
The build accepts the Android SDK licenses to install these packages.
Google publishes the Linux native tools for x86_64, so the ARM64 image provides Java and Gradle without Android tools.

`ANDROID_HOME` and `ANDROID_SDK_ROOT` point to `/opt/android-sdk`.
`ANDROID_NDK_HOME`, `ANDROID_NDK_ROOT`, and `NDK_HOME` point to `/opt/android-ndk`.
`ANDROID_USER_HOME` and `ANDROID_AVD_HOME` locate the configuration and virtual devices under `/root/.android`.
The command line tools, platform tools, emulator, and NDK launchers are on `PATH`.

Two virtual devices are prepared: `android-phone` (Pixel 7) and `android-tablet` (Pixel Tablet).
They share the installed system image and have separate writable device data.
On an AMD64 Linux host with KVM enabled, start either device with:

```bash
podman run --rm -it --device /dev/kvm sakura-dev-tools:latest \
    emulator -avd android-phone -no-window -no-audio -no-boot-anim -gpu swiftshader_indirect
```

Replace `android-phone` with `android-tablet` to start the tablet.
The host user needs permission to access `/dev/kvm`; rootless Podman users may also need `--group-add keep-groups`.
The regular `just container-shell` recipe does not expose KVM, so use the command above when running an emulator.

## Publishing

Woodpecker publishes releases for tags matching `v*`.
The release workflow builds `linux/amd64` and `linux/arm64` through `just build-setup --all`,
then pushes a multi-architecture manifest to both the version tag and `latest` at `ghcr.io/sakura-industries-llc/sakura-dev-tools`.

The Woodpecker repository needs Trusted Security enabled for the privileged Podman build step.
It also needs `ghcr_username` and `ghcr_token` secrets,
where the token is a GitHub classic personal access token with `write:packages` permission.
The workflow registers the ARM64 binfmt handler before it builds the image.

## Updating pinned versions

`tools/outdated.py` reads `mise.toml` and `.env`, including Android and dedicated build-stage pins.
It uses `mise latest` for managed tools and upstream metadata for the remaining image pins.
Java lookups are constrained to Temurin 21; the recorded version remains definite.
Checking updates does not install or upgrade tools in the running container.
With `--update`, it rewrites the pin files in place.

## License

Licensed under Apache-2.0.
See `LICENSE`.
