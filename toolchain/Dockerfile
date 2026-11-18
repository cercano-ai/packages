# Reviewed build recipe; workflow executes only the separately recorded OCI digest.
# Build/publish is a separate manual administrator task, never performed by apt-pages.
FROM debian:bookworm-slim@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc
RUN rm -f /etc/apt/sources.list.d/* && \
    printf '%s\n' \
      'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/20250115T000000Z bookworm main' \
      'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian-security/20250115T000000Z bookworm-security main' \
      > /etc/apt/sources.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends python3 gnupg apt-utils ca-certificates git tar gzip && \
    dpkg-query -W > /toolchain-packages.txt && \
    rm -rf /var/lib/apt/lists/*
# Snapshot metadata is verified using Debian's keyring; HTTP is not a trust anchor.
# No runtime apt/pip/npm/curl installation is permitted in the promotion workflow.
