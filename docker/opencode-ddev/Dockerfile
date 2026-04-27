# Derived OpenCode image with DDEV and Docker CLI for Drupal runtime tasks.
# This extends the base OpenCode image to provide ddev command support.
#
# This Dockerfile uses package-manager detection to work with different base images:
# - Debian/Ubuntu (apt-get)
# - Alpine (apk)
# - Fedora/RHEL/CentOS (dnf/yum)

ARG BASE_IMAGE=ghcr.io/anomalyco/opencode:1.14.25
FROM ${BASE_IMAGE}

# Pinned DDEV version for deterministic builds
ARG DDEV_VERSION=v1.24.3

SHELL ["/bin/sh", "-c"]

# Detect package manager and install minimal dependencies for DDEV and Docker CLI
RUN \
  if command -v apt-get >/dev/null 2>&1; then \
    echo "Detected apt-get (Debian/Ubuntu)" && \
    apt-get update && \
    export DEBIAN_FRONTEND=noninteractive && \
    apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      gnupg \
      git && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*; \
  elif command -v apk >/dev/null 2>&1; then \
    echo "Detected apk (Alpine)" && \
    apk add --no-cache \
      ca-certificates \
      curl \
      gnupg \
      git; \
  elif command -v dnf >/dev/null 2>&1; then \
    echo "Detected dnf (Fedora/RHEL)" && \
    dnf install -y \
      ca-certificates \
      curl \
      gnupg \
      git && \
    dnf clean all; \
  elif command -v yum >/dev/null 2>&1; then \
    echo "Detected yum (RHEL/CentOS)" && \
    yum install -y \
      ca-certificates \
      curl \
      gnupg \
      git && \
    yum clean all; \
  else \
    echo "ERROR: No supported package manager found (apt-get, apk, dnf, yum)" && \
    exit 1; \
  fi

# Install Docker CLI (for in-cage docker commands)
RUN \
  if command -v apt-get >/dev/null 2>&1; then \
    # Debian/Ubuntu: Use Docker's official repository
    # Use /etc/os-release for codename detection instead of lsb_release
    . /etc/os-release && \
    curl -fsSL https://download.docker.com/linux/debian/gpg -o /tmp/docker.gpg && \
    gpg --dearmor < /tmp/docker.gpg > /usr/share/keyrings/docker-archive-keyring.gpg && \
    rm /tmp/docker.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/debian ${VERSION_CODENAME} stable" | tee /etc/apt/sources.list.d/docker.list >/dev/null && \
    apt-get update && \
    export DEBIAN_FRONTEND=noninteractive && \
    apt-get install -y --no-install-recommends docker-ce-cli && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*; \
  elif command -v apk >/dev/null 2>&1; then \
    # Alpine: Use community repository
    apk add --no-cache docker-cli; \
  elif command -v dnf >/dev/null 2>&1; then \
    # Fedora: Use Docker's official repository
    dnf -y install 'dnf-command(config-manager)' && \
    dnf config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo && \
    dnf install -y docker-ce-cli && \
    dnf clean all; \
  elif command -v yum >/dev/null 2>&1; then \
    # RHEL/CentOS: Use Docker's official repository
    yum install -y yum-utils && \
    yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo && \
    yum install -y docker-ce-cli && \
    yum clean all; \
  else \
    echo "ERROR: No supported package manager found for Docker CLI installation" && \
    exit 1; \
  fi

# Install DDEV CLI (deterministic version with checksum verification)
RUN \
  if command -v apt-get >/dev/null 2>&1; then \
    # Debian/Ubuntu: Use official pkg.ddev.com apt repository
    # Use /etc/os-release for codename detection
    . /etc/os-release && \
    curl -fsSL https://pkg.ddev.com/apt/gpg.key.asc -o /tmp/ddev.gpg && \
    gpg --dearmor < /tmp/ddev.gpg > /usr/share/keyrings/ddev.gpg && \
    rm /tmp/ddev.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/ddev.gpg] https://pkg.ddev.com/apt/ * main" | tee /etc/apt/sources.list.d/ddev.list >/dev/null && \
    apt-get update && \
    export DEBIAN_FRONTEND=noninteractive && \
    apt-get install -y --no-install-recommends "ddev=${DDEV_VERSION#v}" && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*; \
  elif command -v apk >/dev/null 2>&1; then \
    # Alpine: Use tarball install with checksum verification
    ARCH=$(case $(uname -m) in x86_64|amd64) echo "amd64" ;; aarch64|arm64) echo "arm64" ;; *) echo "unknown" ;; esac) && \
    if [ "$ARCH" = "unknown" ]; then \
      echo "ERROR: Unsupported architecture: $(uname -m)" && \
      exit 1; \
    fi && \
    curl -fsSL "https://github.com/drud/ddev/releases/download/${DDEV_VERSION}/checksums.txt" -o /tmp/checksums.txt && \
    curl -fsSL "https://github.com/drud/ddev/releases/download/${DDEV_VERSION}/ddev_linux-${ARCH}.${DDEV_VERSION}.tar.gz" -o /tmp/ddev.tar.gz && \
    EXPECTED_SHA=$(grep "ddev_linux-${ARCH}.${DDEV_VERSION}.tar.gz" /tmp/checksums.txt | cut -d' ' -f1) && \
    ACTUAL_SHA=$(sha256sum /tmp/ddev.tar.gz | cut -d' ' -f1) && \
    if [ "$EXPECTED_SHA" != "$ACTUAL_SHA" ]; then \
      echo "ERROR: Checksum verification failed" && \
      echo "Expected: $EXPECTED_SHA" && \
      echo "Actual: $ACTUAL_SHA" && \
      exit 1; \
    fi && \
    tar -xzf /tmp/ddev.tar.gz -C /tmp && \
    mv /tmp/ddev /usr/local/bin/ddev && \
    chmod +x /usr/local/bin/ddev && \
    rm -rf /tmp/ddev.tar.gz /tmp/checksums.txt /tmp/ddev; \
  elif command -v dnf >/dev/null 2>&1; then \
    # Fedora: Use tarball install with checksum verification
    ARCH=$(case $(uname -m) in x86_64|amd64) echo "amd64" ;; aarch64|arm64) echo "arm64" ;; *) echo "unknown" ;; esac) && \
    if [ "$ARCH" = "unknown" ]; then \
      echo "ERROR: Unsupported architecture: $(uname -m)" && \
      exit 1; \
    fi && \
    curl -fsSL "https://github.com/drud/ddev/releases/download/${DDEV_VERSION}/checksums.txt" -o /tmp/checksums.txt && \
    curl -fsSL "https://github.com/drud/ddev/releases/download/${DDEV_VERSION}/ddev_linux-${ARCH}.${DDEV_VERSION}.tar.gz" -o /tmp/ddev.tar.gz && \
    EXPECTED_SHA=$(grep "ddev_linux-${ARCH}.${DDEV_VERSION}.tar.gz" /tmp/checksums.txt | cut -d' ' -f1) && \
    ACTUAL_SHA=$(sha256sum /tmp/ddev.tar.gz | cut -d' ' -f1) && \
    if [ "$EXPECTED_SHA" != "$ACTUAL_SHA" ]; then \
      echo "ERROR: Checksum verification failed" && \
      echo "Expected: $EXPECTED_SHA" && \
      echo "Actual: $ACTUAL_SHA" && \
      exit 1; \
    fi && \
    tar -xzf /tmp/ddev.tar.gz -C /tmp && \
    mv /tmp/ddev /usr/local/bin/ddev && \
    chmod +x /usr/local/bin/ddev && \
    rm -rf /tmp/ddev.tar.gz /tmp/checksums.txt /tmp/ddev; \
  elif command -v yum >/dev/null 2>&1; then \
    # RHEL/CentOS: Use tarball install with checksum verification
    ARCH=$(case $(uname -m) in x86_64|amd64) echo "amd64" ;; aarch64|arm64) echo "arm64" ;; *) echo "unknown" ;; esac) && \
    if [ "$ARCH" = "unknown" ]; then \
      echo "ERROR: Unsupported architecture: $(uname -m)" && \
      exit 1; \
    fi && \
    curl -fsSL "https://github.com/drud/ddev/releases/download/${DDEV_VERSION}/checksums.txt" -o /tmp/checksums.txt && \
    curl -fsSL "https://github.com/drud/ddev/releases/download/${DDEV_VERSION}/ddev_linux-${ARCH}.${DDEV_VERSION}.tar.gz" -o /tmp/ddev.tar.gz && \
    EXPECTED_SHA=$(grep "ddev_linux-${ARCH}.${DDEV_VERSION}.tar.gz" /tmp/checksums.txt | cut -d' ' -f1) && \
    ACTUAL_SHA=$(sha256sum /tmp/ddev.tar.gz | cut -d' ' -f1) && \
    if [ "$EXPECTED_SHA" != "$ACTUAL_SHA" ]; then \
      echo "ERROR: Checksum verification failed" && \
      echo "Expected: $EXPECTED_SHA" && \
      echo "Actual: $ACTUAL_SHA" && \
      exit 1; \
    fi && \
    tar -xzf /tmp/ddev.tar.gz -C /tmp && \
    mv /tmp/ddev /usr/local/bin/ddev && \
    chmod +x /usr/local/bin/ddev && \
    rm -rf /tmp/ddev.tar.gz /tmp/checksums.txt /tmp/ddev; \
  else \
    echo "ERROR: No supported package manager found for DDEV CLI installation" && \
    exit 1; \
  fi

# Provide stable `ddev drush` behavior in cage runtimes.
RUN DDEV_BIN="$(command -v ddev)" && \
  cp "$DDEV_BIN" /usr/local/bin/ddev-real && \
  chmod +x /usr/local/bin/ddev-real && \
  printf '%s\n' '#!/bin/sh' \
    'if [ "$1" = "drush" ]; then' \
    '  shift' \
    '  exec /usr/local/bin/ddev-real exec drush "$@"' \
    'fi' \
    'exec /usr/local/bin/ddev-real "$@"' > /usr/local/bin/ddev && \
  chmod +x /usr/local/bin/ddev

# Verify installations (do not require running Docker daemon)
RUN docker --version && command -v ddev

# Set non-root user (aligned with runtime user 1000)
USER 1000
