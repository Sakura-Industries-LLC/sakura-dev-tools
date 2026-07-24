set shell := ["bash", "-cu"]

set dotenv-load

mod local 'just/local.just'

_default:
    @just --list

# Setup moon for local use/development
moon-setup:
    moon sync config-schemas

# Check if anything is outdated
outdated: (_container-mount "./tools/outdated.py")

# Check if any tools have updates and update to their latest versions
outdated-update: (_container-mount "./tools/outdated.py --update")

# Build container setup
build-setup:
    podman build -f tools/Containerfile.00-base-os -t sakura-dev-tools:00-base-os \
        --build-arg DEBIAN_TAG .
    podman build -f tools/Containerfile.01-proto -t sakura-dev-tools:01-proto \
        --build-arg PROTO_VERSION .
    podman build -f tools/Containerfile.02-rust -t sakura-dev-tools:02-rust \
        --build-arg NEXTEST_VERSION .
    podman build -f tools/Containerfile.03-go -t sakura-dev-tools:03-go \
        --build-arg GOPLS_VERSION .
    podman build -f tools/Containerfile.04-pdf2htmlex-builder \
        -t sakura-dev-tools:04-pdf2htmlex-builder \
        --build-arg PDF2HTMLEX_COMMIT .
    podman build -f tools/Containerfile.05-pdf2htmlex \
        -t sakura-dev-tools:05-pdf2htmlex .
    podman build -f tools/Containerfile.zz-tools -t sakura-dev-tools:latest .
    scripts/report-image-sizes.py
    echo "Cleaning up stale podman layers..."
    podman image prune --force
    echo "DONE!"

# Make sure cache-dirs exist
setup-cache-dir:
    mkdir -p .moon/cache
    mkdir -p .moon/podman-cache

# Fix what can be fixed
fix: setup-cache-dir build-setup (_container-mount "moon run :fix")

# Run a CI run inside Podman.
ci: setup-cache-dir build-setup (_container-mount "moon run :ci")

# Run Fix + CI run inside Podman.
fix-ci: setup-cache-dir build-setup (_container-mount "moon run :fix && moon run :ci")

# Execute a shell directly inside the build container.
container-shell: (_container-mount "bash")

# Common mounted container
_container-mount *cmd:
    podman run --rm \
        -it \
        -v .:/repo:rw \
        -v .moon/podman-cache:/repo/.moon/cache:rw \
        -w /repo \
        sakura-dev-tools:latest \
        bash -c '{{ cmd }}'
