FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
  bash curl git ca-certificates \
  python3 python3-pip python3-venv \
  nodejs npm \
  jq ripgrep \
  && rm -rf /var/lib/apt/lists/*

# Create a non-root user for safety (optional)
RUN useradd -m -s /bin/bash runner
USER runner

WORKDIR /workspaces
CMD ["bash", "-lc", "sleep infinity"]
