set shell := ["bash", "-cu"]

set dotenv-load

mod local 'just/local.just'

_default:
    @just --list

# Setup moon for local use/development
moon-setup:
    moon sync config-schemas

# Check if anything is outdated
outdated:
    ./tools/outdated.py

# Check if any tools have updates and update to their latest versions
outdated-update:
    ./tools/outdated.py --update

# Build container setup
build-setup:
    docker build -f tools/Dockerfile.00-base-os -t sakura-dev-tools:00-base-os \
        --build-arg DEBIAN_TAG .
    docker build -f tools/Dockerfile.01-proto -t sakura-dev-tools:01-proto \
        --build-arg PROTO_VERSION .
    docker build -f tools/Dockerfile.02-rust -t sakura-dev-tools:02-rust \
        --build-arg NEXTEST_VERSION .
    docker build -f tools/Dockerfile.zz-tools -t sakura-dev-tools:latest .

# Make sure cache-dirs exist
setup-cache-dir:
    mkdir -p .moon/cache
    mkdir -p .moon/docker-cache

# Fix what can be fixed
fix: setup-cache-dir build-setup (_container-mount "moon run :fix")

# Run a CI run inside docker.
ci: setup-cache-dir build-setup (_container-mount "moon run :ci")

# Run Fix + CI run inside docker.
fix-ci: setup-cache-dir build-setup (_container-mount "moon run :fix && moon run :ci")

# Execute a shell directly inside the build container.
container-shell: (_container-mount "bash")

# Common mounted container
_container-mount *cmd:
    docker run --rm \
        -it \
        -v .:/repo:rw \
        -v .moon/docker-cache:/repo/.moon/cache:rw \
        -w /repo \
        sakura-dev-tools:latest \
        bash -c '{{ cmd }}'
