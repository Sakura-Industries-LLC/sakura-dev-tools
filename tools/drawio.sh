#!/bin/sh
set -eu

# Electron requires --no-sandbox when running as the container's root user.
# A fresh display allows independent exports to run concurrently.
if [ "$(id -u)" -eq 0 ]; then
    set -- --no-sandbox "$@"
fi
exec xvfb-run -a /usr/bin/drawio --disable-gpu "$@"
