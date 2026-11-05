FROM rust:1.95.0-bookworm@sha256:6258907abe69656e41cd992e0b705cdcfabcbbe3db374f92ed2d47121282d4a1

# Bosn derives a managed image identity from this Dockerfile. Keep this
# project-specific label so an unrelated registry cannot claim the same tag.
LABEL org.opencontainers.image.title="talos-bar-bosn-ci"

# Debian, not Alpine: this is the test image for desktop Qt and Mesa software
# Vulkan (lavapipe) under Xvfb.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        clang lld python3 python3-pip git pkg-config libdbus-1-dev \
        mesa-vulkan-drivers libvulkan1 xvfb xauth libegl1 libgl1 libglib2.0-0 \
        libfontconfig1 libxkbcommon0 libxkbcommon-x11-0 libx11-xcb1 libxcursor1 \
        libxi6 libxrandr2 libxinerama1 libxxf86vm1 libwayland-client0 \
    && rm -rf /var/lib/apt/lists/* \
    && pip3 install --break-system-packages uv==0.9.7 soldr \
    && rustup component add clippy
