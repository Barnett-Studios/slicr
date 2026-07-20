# syntax=docker/dockerfile:1.7
# slicr runtime image — the planner CLI as a container (ADR-0053 image-only parity).
#
# slicr is a pure-stdlib Python reference script, not a PyPI package (the PyPI name is
# taken by an unrelated project, and dotclaude consumes slicr via this image in the
# Python execute-plan loop, not via `pip install` — ADR-0055). So the image IS slicr's
# distributable artifact, exactly as cordon's image is cordon's.
#
# COPY-only over a multi-arch base: no apt, no compile, no arch-specific step — buildx
# assembles the linux/amd64,linux/arm64 manifest with no QEMU emulation.
FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

LABEL org.opencontainers.image.source="https://github.com/Barnett-Studios/slicr" \
      org.opencontainers.image.description="Planner — extract the execution-manifest from a plan file and emit node files" \
      org.opencontainers.image.licenses="MIT OR Apache-2.0"

COPY plan_to_nodes.py /app/plan_to_nodes.py
COPY schema /app/schema

# No user is baked in on purpose (mirrors cordon): slicr writes node files into the
# caller-mounted /work, so the caller supplies `-u $(id -u):$(id -g)` at run time to make
# the emitted files match host ownership. Baking a fixed uid would fight that -u flag and
# leave root/uid-mismatched files in the mounted directory.
WORKDIR /work

# Args: <plan.md> <out-dir>, resolved against the caller-mounted /work
#   docker run --rm -v "$PWD":/work ghcr.io/barnett-studios/slicr plan.md nodes/
ENTRYPOINT ["python", "/app/plan_to_nodes.py"]
