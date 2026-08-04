#!/usr/bin/with-contenv bash
# Add-on entry point. Supervisor writes user options to /data/options.json and
# expects the process to stay in the foreground.
#
# `with-contenv` is not decoration. s6-overlay keeps the container's
# environment aside and only exposes it to processes started through this
# wrapper; under a plain `env bash` the variables Supervisor sets --
# SUPERVISOR_TOKEN among them -- are simply absent, and everything that
# depends on them fails silently rather than loudly.
set -euo pipefail

# Run standalone, /data is whatever the user mounted -- or nothing at all, if
# they just started the image to try it. Creating it keeps that case working
# instead of failing on a missing path; under Supervisor it already exists.
mkdir -p /data

# Supervisor mounts /data owned by root, so its ownership can only be handed
# over from here, before privileges are dropped. Doing it in the Dockerfile has
# no effect: the mount replaces whatever the image had at that path.
chown -R bridge:bridge /data

export PATH="/opt/venv/bin:${PATH}"
export PYTHONPATH="/app"

# s6-setuidgid ships with the base image's s6-overlay and drops privileges
# without leaving a supervising process behind.
exec s6-setuidgid bridge python3 -m bridge
