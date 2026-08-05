# syntax=docker/dockerfile:1
#
# Two stages: everything needed to *build* the Python environment stays in the
# first one, so compilers, headers and git never reach the running image.
#
# The vendor SDK ships a closed-source shared library linked against glibc
# (libc.so.6, libstdc++.so.6, symbols up to GLIBC_2.25). Home Assistant's own
# container is Alpine/musl and cannot load it, which is why this bridge exists
# as a separate add-on rather than living inside the integration -- and why the
# base image here is Debian rather than the usual Alpine.
ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-debian:bookworm

# ---------------------------------------------------------------------------
# Stage 1: build the Python environment
# ---------------------------------------------------------------------------
FROM ${BUILD_FROM} AS builder

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-venv \
        # pip resolves `git+https://` requirements by shelling out to git, and
        # the vendor SDK is only distributed from a git repository.
        git \
    && rm -rf /var/lib/apt/lists/*

ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv "${VIRTUAL_ENV}"
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# The SDK wheel bundles prebuilt libraries for every platform it supports; only
# the one matching this image is reachable at runtime.
ARG TARGETARCH
RUN set -eux; \
    LIBS="$(python3 -c 'import miot, pathlib; print(pathlib.Path(miot.__file__).parent / "libs")')"; \
    case "${TARGETARCH:-amd64}" in \
        amd64) KEEP="x86_64" ;; \
        arm64) KEEP="arm64" ;; \
        *) echo "unsupported architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    find "${LIBS}" -mindepth 1 -maxdepth 1 -not -name linux -exec rm -rf {} +; \
    find "${LIBS}/linux" -mindepth 1 -maxdepth 1 -not -name "${KEEP}" -exec rm -rf {} +; \
    python3 -c 'from miot.camera import _load_dynamic_lib; print("native library:", _load_dynamic_lib().miot_camera_version().decode())'

# A misread method name once shipped because nothing verifies that the calls
# the bridge makes exist: the tests avoid importing the SDK by design, and no
# linter resolves attributes across a third-party package. Failing the build
# here is the only place that check costs nothing extra.
COPY check_sdk_api.py /tmp/check_sdk_api.py
COPY rootfs/app/bridge /tmp/bridge
RUN python3 /tmp/check_sdk_api.py /tmp/bridge \
    && rm -rf /tmp/bridge /tmp/check_sdk_api.py

# Strip caches the running image never reads.
RUN find "${VIRTUAL_ENV}" -name "__pycache__" -type d -prune -exec rm -rf {} + \
    && find "${VIRTUAL_ENV}" -name "*.pyc" -delete

# ---------------------------------------------------------------------------
# Stage 2: fetch a static ffmpeg
# ---------------------------------------------------------------------------
FROM ${BUILD_FROM} AS ffmpeg

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG TARGETARCH
ARG FFMPEG_TAG="autobuild-2026-08-03-14-02"
ARG FFMPEG_BUILD="ffmpeg-n7.1.5-12-g1fdbca85aa"

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl xz-utils ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Debian's ffmpeg package pulls in over a hundred shared libraries -- font
# rendering, SVG, ICU -- none of which matter for repackaging a stream. A static
# build is one file and drops roughly 400 MB from the running image.
#
# The GPL variant is deliberate: this add-on encodes as well as copies -- the
# compatibility and low-bandwidth streams are produced here -- and libx264 and
# libx265 are the standard encoders for those formats. Both are excluded from
# LGPL builds. ffmpeg runs as a separate process invoked over a command line,
# so its licence does not reach this project's own Apache-2.0 code; the
# obligation it does carry, to point at the build's sources, is met in README.
COPY ffmpeg.sha256 /tmp/ffmpeg.sha256
RUN set -eux; \
    case "${TARGETARCH:-amd64}" in \
        amd64) FF_ARCH="linux64" ;; \
        arm64) FF_ARCH="linuxarm64" ;; \
        *) echo "unsupported architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    ARCHIVE="${FFMPEG_BUILD}-${FF_ARCH}-gpl-7.1.tar.xz"; \
    curl -fsSL -o "/tmp/${ARCHIVE}" \
        "https://github.com/BtbN/FFmpeg-Builds/releases/download/${FFMPEG_TAG}/${ARCHIVE}"; \
    # Verified against a checksum committed to this repository: pinning the URL
    # alone still trusts whatever it serves at build time.
    grep " ${ARCHIVE}\$" /tmp/ffmpeg.sha256 | sed "s| ${ARCHIVE}\$|  /tmp/${ARCHIVE}|" | sha256sum -c -; \
    tar -xJf "/tmp/${ARCHIVE}" -C /tmp; \
    install -m 0755 "/tmp/${FFMPEG_BUILD}-${FF_ARCH}-gpl-7.1/bin/ffmpeg" /usr/local/bin/ffmpeg; \
    rm -rf /tmp/*; \
    /usr/local/bin/ffmpeg -version | head -1; \
    # The published streams are encoded with x264 and x265, which exist only in
    # GPL builds. Checked here so a build that lost them fails now rather than
    # when a user opens a camera that will not play.
    #
    # Written out before it is searched, rather than piped into `grep -q`.
    # With `pipefail` set, grep exiting on its first match closes the pipe,
    # ffmpeg dies of SIGPIPE, and the build fails with 141 -- precisely when
    # the encoder *is* there.
    /usr/local/bin/ffmpeg -hide_banner -encoders > /tmp/encoders.txt 2>/dev/null; \
    grep -q libx264 /tmp/encoders.txt; \
    grep -q libx265 /tmp/encoders.txt; \
    rm -f /tmp/encoders.txt

# ---------------------------------------------------------------------------
# Stage 3: the running image
# ---------------------------------------------------------------------------
FROM ${BUILD_FROM}

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG GO2RTC_VERSION="1.9.9"
ARG TARGETARCH

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:${PATH}"

# Only what the bridge needs at run time. libstdc++6 is required by the vendor's
# native library; curl is used below and removed again.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        libstdc++6 \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

# go2rtc turns the raw elementary stream into RTSP, WebRTC and HLS, so one
# camera session serves Home Assistant, a browser and an external NVR alike.
COPY go2rtc.sha256 /tmp/go2rtc.sha256
RUN set -eux; \
    case "${TARGETARCH:-amd64}" in \
        amd64) GO2RTC_ARCH="amd64" ;; \
        arm64) GO2RTC_ARCH="arm64" ;; \
        *) echo "unsupported architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /usr/local/bin/go2rtc \
        "https://github.com/AlexxIT/go2rtc/releases/download/v${GO2RTC_VERSION}/go2rtc_linux_${GO2RTC_ARCH}"; \
    grep " go2rtc_linux_${GO2RTC_ARCH}\$" /tmp/go2rtc.sha256 \
        | sed "s| go2rtc_linux_${GO2RTC_ARCH}\$|  /usr/local/bin/go2rtc|" \
        | sha256sum -c -; \
    rm -f /tmp/go2rtc.sha256; \
    chmod +x /usr/local/bin/go2rtc; \
    go2rtc --version; \
    apt-get purge -y --auto-remove curl; \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
COPY --from=ffmpeg /usr/local/bin/ffmpeg /usr/local/bin/ffmpeg
COPY rootfs /

# The bridge itself runs unprivileged -- it opens only unprivileged ports and
# writes only to /data. That limits the blast radius of a memory-safety bug in
# the closed-source streaming library, in ffmpeg or in go2rtc, all of which
# parse attacker-influenceable video on the host network namespace.
#
# The container still starts as root: Supervisor mounts /data owned by root, and
# only root can hand it to the unprivileged user. run.sh drops privileges once
# that is done.
RUN useradd --system --uid 1000 --home-dir /data --shell /usr/sbin/nologin bridge

LABEL \
    io.hass.name="Xiaomi Camera Bridge" \
    io.hass.description="Streams Xiaomi cameras as RTSP for Home Assistant" \
    io.hass.type="addon"

CMD ["/app/run.sh"]
